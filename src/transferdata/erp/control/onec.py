"""Read-only 1C metadata import and deterministic SQL mappings, never guessed."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from transferdata.erp.engine.exceptions import ETLError
from transferdata.erp.engine.validators import identifier
from transferdata.web.maintenance import is_paused
from .models import DatabaseConnection, OneCObject, OneCScan


def connection_fingerprint(connection):
    values = [connection.server, connection.port, connection.database, connection.authentication_type,
              connection.username, connection.credential_reference, connection.trust_server_certificate]
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


def read_metadata(connection, name=None, *, names=None, include_table_parts=False):
    manifest_path = settings.STATE_DIR / 'onec-helper.json'
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
        command = [manifest['dotnet'], manifest['assembly']]
        if manifest.get('protocol') != 1 or not all(Path(path).is_file() for path in command):
            raise ValueError()
    except (OSError, ValueError, KeyError, TypeError):
        raise ETLError('Помощник 1С не установлен. Выполните windows\\Install-1CMetadata.ps1 на сервере портала.') from None
    password = ''
    if connection.authentication_type == 'SQL':
        password = os.environ.get(connection.credential_reference, '')
        if not password:
            raise ETLError('Пароль SQL-подключения не задан в окружении ERP-worker.')
    request = {'action': 'object' if name else 'catalog', 'server': connection.server,
               'port': connection.port, 'database': connection.database, 'authentication': connection.authentication_type,
               'username': connection.username, 'password': password, 'trustCertificate': connection.trust_server_certificate,
               'timeout': connection.connection_timeout, 'name': name or ''}
    if include_table_parts:
        request['includeTableParts'] = True
    if names is not None:
        if name or not isinstance(names, list) or not 1 <= len(names) <= 64 or not all(isinstance(n, str) and n for n in names):
            raise ETLError('Для пакетного чтения укажите от 1 до 64 имён объектов.')
        request.update(action='objects', names=names)
    try:
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(command, input=json.dumps(request).encode('utf-8'), stdout=output,
                                    stderr=subprocess.DEVNULL, timeout=600, check=False, shell=False)
            output.seek(0)
            content = output.read(16 * 1024 * 1024 + 1)
        if len(content) > 16 * 1024 * 1024:
            raise ETLError('Метаданные превышают допустимый размер ответа.')
        data = json.loads(content)
        if result.returncode or data.get('protocol') != 1 or data.get('ok') is not True:
            raise ValueError()
        return data
    except subprocess.TimeoutExpired:
        raise ETLError('Чтение метаданных превысило 10 минут. Проверьте доступность базы и совместимость конфигурации.') from None
    except (OSError, ValueError, TypeError, AttributeError):
        raise ETLError('Не удалось прочитать метаданные 1С. Проверьте права SELECT на служебные таблицы, TLS и совместимость версии 1С.') from None


def mapping_from_metadata(data):
    """Alias physical columns; keep reference bytes, dates and union components intact."""
    table = data.get('table', '')
    identifier(table)
    columns, used, physical = [], set(), set()
    properties = data.get('properties')
    if not isinstance(properties, list):
        raise ValidationError('Структура объекта ещё не загружена.')
    for prop in properties:
        name = prop.get('name', '')
        if not isinstance(name, str) or not name:
            raise ValidationError('Неизвестное имя реквизита.')
        parts = prop.get('columns', [])
        for part in parts:
            source = part['name']
            identifier(source)
            if source.casefold() in physical:
                raise ValidationError('Метаданные содержат повторяющийся физический столбец.')
            physical.add(source.casefold())
            label = name if len(parts) == 1 else name + '__' + part['purpose']
            alias = re.sub(r'[^\w$@#]', '_', label)
            if not alias or not re.match(r'\w', alias[0]):
                alias = '_' + alias
            candidate, counter = alias[:128], 0
            while candidate.casefold() in used:
                suffix = hashlib.sha256(f'{source}:{counter}'.encode()).hexdigest()[:8]
                candidate = alias[:119] + '_' + suffix
                counter += 1
            identifier(candidate)
            used.add(candidate.casefold())
            columns.append({'source': source, 'alias': candidate, 'property': name,
                            'purpose': part.get('purpose', ''), 'type': part.get('type', ''),
                            'row_version': part.get('row_version') is True,
                            'is_date': part.get('is_date') is True})
    if not columns:
        raise ValidationError('У объекта нет поддерживаемых физических столбцов.')
    digest = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    mapping = {'schema': 'dbo', 'table': table, 'columns': columns, 'metadata_hash': digest,
               'name': data.get('name', ''), 'year_offset': data.get('year_offset', 0)}
    if data.get('reference_version') == 1:
        from .onec_references import reference_mapping
        mapping['references'], mapping['reference_warnings'] = reference_mapping(data, columns)
        mapping['reference_version'] = 1
    return mapping


def validate_object(obj, connection):
    if obj is None or connection is None or obj.connection_id != connection.pk or not obj.is_active:
        raise ValidationError({'one_c_object': 'Выберите действующий объект 1С из выбранного подключения.'})
    if obj.fingerprint != connection_fingerprint(connection) or not obj.metadata:
        raise ValidationError({'one_c_object': 'Прочитайте актуальную структуру объекта в разделе «Метаданные 1С».'})
    return mapping_from_metadata(obj.metadata)


def source_query(mapping, adjust_dates=False, resolve_references=False):
    fields = mapping.get('columns', [])
    if not fields:
        raise ValidationError('Отсутствует сохранённое соответствие столбцов 1С.')
    joins, extra, raw_aliases = [], [], {}
    if resolve_references:
        if mapping.get('reference_version') != 1:
            raise ValidationError('Для расшифровки ссылок повторно получите структуру объекта 1С и сохраните задание.')
        from .onec_references import reference_select
        joins, extra, raw_aliases = reference_select(mapping, adjust_dates)
    def expression(column):
        value = ('[src].' if resolve_references else '') + identifier(column['source'])
        if column.get('row_version'):
            value = 'CONVERT(binary(8), ' + value + ')'
        elif adjust_dates and column.get('is_date'):
            value = f'CASE WHEN YEAR({value}) < 3753 THEN {value} ELSE DATEADD(year, -2000, {value}) END'
        return value + ' AS ' + identifier(raw_aliases.get(column['source'], column['alias']))
    columns = ',\n    '.join([expression(column) for column in fields] + extra)
    table = f"{identifier(mapping['schema'])}.{identifier(mapping['table'])}"
    if resolve_references:
        table += ' AS [src]'
    return f"SELECT\n    {columns}\nFROM {table}" + (('\n' + '\n'.join(joins)) if joins else '')


@transaction.atomic
def enqueue_scan(connection, obj=None):
    if is_paused():
        raise ValidationError('Портал обновляется. Повторите позже.')
    connection = DatabaseConnection.objects.select_for_update().get(pk=connection.pk)
    if not connection.is_active or connection.role == 'TARGET':
        raise ValidationError('Выберите активное подключение источника.')
    if obj is not None and obj.connection_id != connection.pk:
        raise ValidationError('Объект относится к другому подключению.')
    try:
        with transaction.atomic():
            return OneCScan.objects.create(connection=connection, object=obj), True
    except IntegrityError:
        return OneCScan.objects.get(connection=connection, status__in=['PENDING', 'RUNNING']), False


def scan_next():
    if is_paused():
        return False
    scan = OneCScan.objects.filter(status='PENDING').select_related('connection', 'object').order_by('created_at', 'pk').first()
    if scan is None or not OneCScan.objects.filter(pk=scan.pk, status='PENDING').update(
            status='RUNNING', started_at=timezone.now(), phase='READING'):
        return False
    fingerprint = connection_fingerprint(scan.connection)
    try:
        data = (read_metadata(scan.connection, scan.object.name, include_table_parts=True)
                if scan.object_id else read_metadata(scan.connection, None))
        if scan.object_id:
            from .onec_structure import enrich_attributes
            enrich_attributes(scan.connection, data)
        OneCScan.objects.filter(pk=scan.pk).update(phase='SAVING')
        with transaction.atomic():
            current = DatabaseConnection.objects.select_for_update().get(pk=scan.connection_id)
            if connection_fingerprint(current) != fingerprint or not current.is_active or current.role == 'TARGET':
                raise ETLError('Подключение изменилось во время чтения. Повторите чтение метаданных.')
            if scan.object_id:
                if data.get('name') != scan.object.name:
                    raise ETLError('Получена структура другого объекта. Импорт отменён.')
                mapping_from_metadata(data)
                OneCObject.objects.filter(pk=scan.object_id).update(metadata=data, fingerprint=fingerprint,
                    is_active=True, updated_at=timezone.now())
                message = 'Структура получена. Можно создать задание по объекту 1С.'
            else:
                objects = data.get('objects')
                if not isinstance(objects, list) or not objects or len(objects) > 100000:
                    raise ETLError('Каталог 1С пуст или не поддерживается. Предыдущий каталог сохранён.')
                names = set()
                for entry in objects:
                    name, kind = entry['name'], entry['kind']
                    if not isinstance(name, str) or not isinstance(kind, str) or len(name) > 257 or len(kind) > 64 or name in names or not name.startswith(kind + '.'):
                        raise ETLError('Некорректный каталог 1С. Импорт отменён.')
                    names.add(name)
                OneCObject.objects.filter(connection=scan.connection).update(is_active=False)
                for entry in objects:
                    obj, _ = OneCObject.objects.get_or_create(connection=scan.connection, name=entry['name'],
                        defaults={'kind': entry['kind']})
                    if obj.fingerprint != fingerprint:
                        obj.metadata = {}
                    obj.fingerprint, obj.kind, obj.is_active = fingerprint, entry['kind'], True
                    obj.save(update_fields=['fingerprint', 'kind', 'is_active', 'metadata', 'updated_at'])
                message = f"Каталог прочитан: {len(objects)} объектов. Конфигурация: {data.get('configuration', '')}. Выберите объект и получите его структуру."
        scan.status, scan.message = 'SUCCESS', message
    except Exception as exc:
        scan.status = 'FAILED'
        scan.message = str(exc) if isinstance(exc, ETLError) else 'Не удалось обработать метаданные. Предыдущие соответствия сохранены.'
    scan.finished_at = timezone.now()
    scan.save(update_fields=['status', 'message', 'finished_at'])
    return True
