from pathlib import Path
import pyodbc
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from transferdata.erp.control.models import ETLJob, ETLAuditLog
from transferdata.erp.control.onec import read_metadata, connection_fingerprint
from transferdata.erp.control.management.commands.prepare_payment_schedule_report import Command as PaymentCommand
from transferdata.erp.engine.connections import connect
from transferdata.erp.engine.extractor import metadata as describe
from transferdata.erp.engine.exceptions import ETLError
from transferdata.erp.reports.additional_attributes import parts, prop, discovery_sql, generate


class Command(BaseCommand):
    object_label = 'Сотрудники'
    help = 'Prepare employee export with all used additional properties as named columns; source is read-only.'

    def add_arguments(self, parser):
        parser.add_argument('--connection')
        parser.add_argument('--target-connection')
        parser.add_argument('--object', default='Справочник.Сотрудники')
        parser.add_argument('--target-schema', default='erp_ai')
        parser.add_argument('--target-table', default='EmployeesExpanded')
        parser.add_argument('--output', default='.state/EmployeesExpanded.sql')
        parser.add_argument('--create-job', action='store_true')

    def handle(self, *args, **options):
        source = PaymentCommand.pick(options['connection'], source=True)
        target = PaymentCommand.pick(options['target_connection'], source=False) if options['create_job'] else None
        output = Path(options['output']).resolve()
        if output.exists():
            raise CommandError('SQL-файл уже существует. Укажите другое имя через --output.')
        fingerprint = connection_fingerprint(source)
        try:
            self.stdout.write(f'[1/3] Читаем {options["object"]} и структуру дополнительных реквизитов…')
            root = read_metadata(source, options['object'], include_table_parts=True)
            references = prop(parts(root), 'Свойство').get('references', [])
            if len(references) != 1:
                raise CommandError('Не определён единственный объект описания дополнительных свойств.')
            definitions = read_metadata(source, references[0])
            if (root.get('configuration'), root.get('version')) != (definitions.get('configuration'), definitions.get('version')):
                raise CommandError('Конфигурация изменилась во время чтения. Повторите подготовку.')
            self.stdout.write('[2/3] Читаем названия дополнительных свойств объекта…')
            with connect(source) as database:
                cursor = database.cursor()
                try:
                    cursor.execute(discovery_sql(root, definitions))
                    attributes = cursor.fetchmany(1001)
                finally:
                    cursor.close()
                if len(attributes) > 1000:
                    raise CommandError('Слишком много дополнительных свойств; разделите выгрузку.')
                names = {}
                for identity, name in attributes:
                    key = bytes(identity)
                    if key in names and names[key] != name:
                        raise CommandError('Названия одного свойства различаются по областям данных; нужна отдельная выгрузка областей.')
                    names[key] = name
                sql = generate(root, list(names.items()))
                self.stdout.write('[3/3] Проверяем SELECT и названия колонок…')
                columns = describe(database, sql)
            source.refresh_from_db()
            if connection_fingerprint(source) != fingerprint:
                raise CommandError('Подключение изменилось. Повторите подготовку.')
            if target and ETLJob.objects.filter(target_connection=target, target_schema=options['target_schema'], target_table=options['target_table'], is_deleted=False).exists():
                raise CommandError('Задание для этой таблицы уже существует. Дубликат не создан.')
            with transaction.atomic():
                job = None
                if target:
                    job = ETLJob(name=self.object_label + ' · все дополнительные реквизиты', source_connection=source,
                        target_connection=target, source_type='SQL_QUERY', sql_query=sql,
                        target_schema=options['target_schema'], target_table=options['target_table'], schedule_enabled=False,
                        description='Одна строка на объект справочника. Все используемые дополнительные свойства; исходные компоненты и ссылки сохранены. Новые свойства требуют повторной подготовки SELECT. Повтор свойства у объекта вызывает ошибку вместо потери значения.')
                    job.full_clean(); job.save()
                    ETLAuditLog.objects.create(object_type='ETLJob', object_id=job.pk, action='create')
                output.parent.mkdir(parents=True, exist_ok=True)
                with output.open('x', encoding='utf-8') as stream:
                    stream.write(sql + '\n')
            self.stdout.write(self.style.SUCCESS(f'Подготовлено свойств: {len(names)}; колонок: {len(columns)}. SQL: {output}'))
            if job:
                self.stdout.write(f'Создано задание #{job.pk}: {job.target_schema}.{job.target_table}. Расписание выключено; запустите вручную в портале.')
            self.stdout.write('Свойства без строк в табличной части не включены. Новые свойства потребуют повторной подготовки. Сверьте результат с 1С перед расписанием.')
        except (ValidationError, ETLError) as exc:
            raise CommandError(' '.join(exc.messages) if isinstance(exc, ValidationError) else str(exc)) from None
        except pyodbc.Error:
            raise CommandError('SQL Server не подтвердил запрос. Проверьте метаданные и права SELECT; исходные данные не изменялись.') from None
