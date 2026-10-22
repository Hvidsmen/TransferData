"""Export only configuration metadata required to translate a 1C report."""
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from transferdata.erp.control.models import DatabaseConnection, OneCObject
from transferdata.erp.control.onec import connection_fingerprint, read_metadata
from transferdata.erp.engine.exceptions import ETLError
from transferdata.erp.reports import cash_receipts


class Command(BaseCommand):
    help = 'Export read-only 1C metadata for cash_receipts; never exports records, passwords or connection strings.'

    def add_arguments(self, parser):
        parser.add_argument('--connection', help='Exact source connection name; optional when only one source is active.')
        parser.add_argument('--report', choices=['cash_receipts'], default='cash_receipts')
        parser.add_argument('--output', required=True)
        parser.add_argument('--use-cache', action='store_true', help='Use valid cached structures when available.')

    def handle(self, *args, **options):
        candidates = DatabaseConnection.objects.filter(is_active=True).exclude(role='TARGET')
        if options['connection']:
            candidates = candidates.filter(name=options['connection'])
        selected = list(candidates[:2])
        if not selected:
            raise CommandError('Активное подключение источника не найдено.')
        if len(selected) != 1:
            raise CommandError('Доступно несколько источников. Добавьте --connection "Название подключения" из портала.')
        connection = selected[0]
        if connection.role == 'TARGET':
            raise CommandError('Выберите подключение источника, а не назначения.')
        output = Path(options['output']).resolve()
        if output.exists():
            raise CommandError('Выходной файл уже существует. Укажите новое имя, чтобы не перезаписать данные.')
        output.parent.mkdir(parents=True, exist_ok=True)
        fingerprint = connection_fingerprint(connection)
        objects, issues = {}, []
        visited = set()

        def fetch(name):
            if name in visited:
                return objects.get(name)
            visited.add(name)
            self.stdout.write(f'Чтение метаданных: {name}')
            data = None
            if options['use_cache']:
                cached = OneCObject.objects.filter(connection=connection, name=name, is_active=True,
                    fingerprint=fingerprint).first()
                if cached and cached.metadata:
                    data = cached.metadata
            if data is None:
                try:
                    data = read_metadata(connection, name)
                except ETLError as exc:
                    issues.append({'object': name, 'message': str(exc)})
                    self.stderr.write(f'Не получено: {name}. {exc}')
                    return None
            if data.get('name') != name or not isinstance(data.get('properties'), list):
                issues.append({'object': name, 'message': 'Помощник вернул структуру другого объекта или неподдерживаемый формат.'})
                return None
            objects[name] = data
            return data

        def follow(name, path):
            key = (name, tuple(path))
            if key in followed:
                return
            followed.add(key)
            data = fetch(name)
            if data is None:
                return
            prop = next((p for p in data['properties'] if p.get('name') == path[0]), None)
            if prop is None:
                issues.append({'object': name, 'path': '.'.join(path), 'message': 'Реквизит отсутствует в полученных метаданных.'})
                return
            if len(path) == 1:
                return
            references = prop.get('references', [])
            concrete = [ref for ref in references if isinstance(ref, str) and len(ref.split('.')) == 2
                        and ref.split('.')[0] in ('Справочник', 'Документ', 'ПланВидовХарактеристик', 'ПланСчетов')]
            if len(concrete) != len(references) or not concrete:
                issues.append({'object': name, 'path': '.'.join(path),
                    'message': 'Есть обобщённый или неразрешённый тип ссылки. Для точного SQL требуется уточнение типа.'})
            for target in concrete:
                follow(target, path[1:])

        followed = set()
        if not options['use_cache']:
            self.stdout.write('Пакетное чтение основных объектов отчёта…')
            try:
                batch = read_metadata(connection, names=cash_receipts.OBJECTS)
                for data in batch.get('objects', []):
                    name = data.get('name')
                    if name in cash_receipts.OBJECTS and isinstance(data.get('properties'), list):
                        objects[name] = data
                        visited.add(name)
            except ETLError:
                self.stderr.write('Пакетное чтение недоступно. Проверяем объекты по одному, чтобы сохранить доступные структуры.')
        for name in cash_receipts.OBJECTS:
            fetch(name)
        for name, path in cash_receipts.PATHS:
            follow(name, path)
        connection.refresh_from_db()
        if connection_fingerprint(connection) != fingerprint:
            raise CommandError('Параметры подключения изменились во время чтения. Повторите экспорт.')
        payload = {'format': 'etl-dev.onec-report-metadata', 'version': 1, 'report': options['report'],
            'source': {'connection': connection.name, 'database': connection.database},
            'required_parameters': cash_receipts.PARAMETERS,
            'suggested_target': {'schema': 'erp_ai', 'table': 'CashReceipts', 'report_date_format': 'yyyyMMdd'},
            'objects': objects, 'issues': issues,
            'notes': ['Файл содержит структуру конфигурации, не записи таблиц.',
                      'ВерсииОбъектов: финальный INNER JOIN исходного отчёта исключает документы без версий.',
                      'Нужен UUID предопределённого вида соответствия СтруктураПредприятия_ЦФО.',
                      'Для бит_ОборотыПоБюджетам нужно сверить знак оборота и границы периода с 1С.']}
        # Exclusive creation also protects against a file appearing after the initial check.
        with output.open('x', encoding='utf-8', newline='\n') as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        self.stdout.write(self.style.SUCCESS(f'Экспортировано объектов: {len(objects)}. Требуют уточнения: {len(issues)}.'))
        self.stdout.write(str(output))
        self.stdout.write('Рабочие задания и сохранённые структуры портала не изменялись.')
