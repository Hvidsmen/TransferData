"""Prepare the approved cash-receipts export and optionally register a manual job."""
import csv
import json
from pathlib import Path

import pyodbc
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from transferdata.erp.control.models import DatabaseConnection, ETLJob, ETLAuditLog
from transferdata.erp.control.onec import read_metadata, connection_fingerprint
from transferdata.erp.engine.connections import connect
from transferdata.erp.engine.extractor import metadata as describe
from transferdata.erp.engine.exceptions import ETLError
from transferdata.erp.reports.cash_receipts import OBJECTS
from transferdata.erp.reports.cash_receipts_sql import generate, settings_from_rows
from transferdata.erp.reports.cash_receipts_choices import Metadata, DDS, BUDGET, KINDS


class Command(BaseCommand):
    help = 'Prepare CashReceipts for the three agreed articles, including historical Works; optionally create a manual job.'

    def add_arguments(self, parser):
        parser.add_argument('--metadata', default='.state/cash-receipts-metadata.json')
        parser.add_argument('--choices', default='.state/cash-receipts-choices.csv')
        parser.add_argument('--output', default='.state/CashReceipts.sql')
        parser.add_argument('--connection')
        parser.add_argument('--target-connection')
        parser.add_argument('--area', type=int, default=0)
        parser.add_argument('--create-job', action='store_true')

    def handle(self, *args, **options):
        output = Path(options['output']).resolve()
        if output.exists():
            raise CommandError('SQL-файл уже существует. Укажите новое имя через --output.')
        try:
            payload = json.loads(Path(options['metadata']).read_text(encoding='utf-8-sig'))
            with Path(options['choices']).open(encoding='utf-8-sig', newline='') as stream:
                selection = settings_from_rows(list(csv.DictReader(stream, delimiter=';')), options['area'])
            if payload.get('format') != 'etl-dev.onec-report-metadata' or payload.get('version') != 1 or payload.get('report') != 'cash_receipts' or payload.get('issues'):
                raise CommandError('Нужен полный экспорт метаданных cash_receipts без ошибок.')
            source_info = payload['source']
            source = self.pick(options['connection'] or source_info['connection'], source=True)
            if source.database.casefold() != source_info['database'].casefold():
                raise CommandError('База источника отличается от экспорта метаданных.')
            fingerprint = connection_fingerprint(source)
            self.stdout.write('Уточняем измерения и ресурсы бюджетного регистра…')
            batch = read_metadata(source, names=[OBJECTS[2], DDS, BUDGET, KINDS])
            refreshed = {obj['name']: obj for obj in batch['objects']}
            fresh = refreshed[OBJECTS[2]]
            old = payload['objects'][OBJECTS[2]]
            for field in ('name', 'table', 'configuration', 'version', 'partitions'):
                if old.get(field) != fresh.get(field):
                    raise CommandError('Конфигурация изменилась. Повторите экспорт метаданных и списка статей.')
            # All physical components must still match; only the newly exported role flags may differ.
            def layout(obj):
                return [(p['name'], p['columns'], p.get('references')) for p in obj['properties']]
            if layout(old) != layout(fresh):
                raise CommandError('Структура регистра изменилась. Повторите экспорт метаданных.')
            payload['objects'].update(refreshed)
            sql = generate(payload['objects'], selection)
            self.stdout.write('Проверяем структуру SELECT на SQL Server (без выполнения выгрузки)…')
            with connect(source, timeout=300) as database:
                # The CSV is not trusted as a connection identity: check every selected ID/code in this source.
                m = Metadata(payload['objects'])
                cursor = database.cursor()
                try:
                    checks = [(name, code, selection['articles'][name][category])
                              for name in (DDS, BUDGET)
                              for code, category in [('00-000012', 'Goods'), ('00-000014', 'Works'), ('00-000017', 'OCM')]]
                    checks.append((KINDS, 'СтруктураПредприятия_ЦФО', selection['kind']))
                    for name, code, reference in checks:
                        cursor.execute(f"SELECT COUNT_BIG(*) FROM {m.table(name)} AS [c] WHERE {m.column(name, 'Ссылка', 'c')} = ? AND {m.column(name, 'Код', 'c')} = ? AND {m.area(name, 'c')} = ?",
                                       bytes.fromhex(reference[2:]), code, selection['area'])
                        if cursor.fetchone()[0] != 1:
                            raise CommandError(f'Выбранная статья/настройка {code} не найдена в источнике. Повторите экспорт списка статей.')
                finally:
                    cursor.close()
                columns = describe(database, sql)
            source.refresh_from_db()
            if connection_fingerprint(source) != fingerprint:
                raise CommandError('Подключение изменилось. Повторите подготовку.')
            target = self.pick(options['target_connection'], source=False) if options['create_job'] else None
            if target and ETLJob.objects.filter(target_connection=target, target_schema='erp_ai', target_table='CashReceipts', is_deleted=False).exists():
                raise CommandError('Задание для erp_ai.CashReceipts уже существует. Измените его в портале; дубликат не создан.')
            job = None
            with transaction.atomic():
                if target:
                    job = ETLJob(name='Денежные поступления', source_connection=source, target_connection=target,
                        source_type='SQL_QUERY', sql_query=sql, target_schema='erp_ai', target_table='CashReceipts',
                        load_mode='FULL', command_timeout=1800, schedule_enabled=False,
                        description='Goods 00-000012; Works 00-000014 (включая исторические); OCM 00-000017. Вся история по Москве. Документы без версий исключаются. Сверить суммы с 1С перед расписанием.')
                    job.full_clean()
                    job.save()
                    ETLAuditLog.objects.create(object_type='ETLJob', object_id=job.pk, action='create')
                output.parent.mkdir(parents=True, exist_ok=True)
                with output.open('x', encoding='utf-8', newline='\n') as stream:
                    stream.write(sql + '\n')
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise CommandError(f'Не удалось подготовить отчёт: {type(exc).__name__}. Проверьте пути и формат файлов.') from None
        except (ValidationError, ETLError) as exc:
            raise CommandError(' '.join(exc.messages) if isinstance(exc, ValidationError) else str(exc)) from None
        except pyodbc.Error:
            raise CommandError('SQL Server не подтвердил структуру отчёта. Проверьте подключение и актуальность метаданных; задание не создано.') from None
        self.stdout.write(self.style.SUCCESS(f'SELECT проверен: {len(columns)} столбцов. SQL: {output}'))
        if job:
            self.stdout.write(self.style.SUCCESS(f'Создано задание #{job.pk}: Денежные поступления → erp_ai.CashReceipts.'))
            self.stdout.write('Откройте «1С Перенос данных → Задания» и выполните первый запуск вручную. Расписание выключено.')
        self.stdout.write('На источнике данные не изменялись. Перед расписанием сверьте количество строк и суммы с отчётом 1С.')

    @staticmethod
    def pick(name, *, source):
        candidates = DatabaseConnection.objects.filter(is_active=True).exclude(role='TARGET' if source else 'SOURCE')
        if name:
            candidates = candidates.filter(name=name)
        choices = list(candidates[:2])
        if len(choices) != 1:
            option = '--connection' if source else '--target-connection'
            raise CommandError(f'Укажите одно активное подключение через {option} "Название из портала".')
        return choices[0]
