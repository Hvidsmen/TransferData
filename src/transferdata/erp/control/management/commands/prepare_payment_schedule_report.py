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
from transferdata.erp.reports.payment_schedule import REGISTER, REGISTRY, concrete_references, generate


class Command(BaseCommand):
    help = 'Prepare daily payment schedule, all history and future dates, with original currency and USD.'

    def add_arguments(self, parser):
        parser.add_argument('--connection')
        parser.add_argument('--target-connection')
        parser.add_argument('--output', default='.state/PaymentScheduleReport.sql')
        parser.add_argument('--target-schema', default='erp_ai')
        parser.add_argument('--target-table', default='PaymentScheduleReport')
        parser.add_argument('--usd-resource', help='Exact stored management-amount resource; auto-detects СуммаУпр or СуммаПлатежаУпр when unambiguous.')
        parser.add_argument('--inspect', action='store_true', help='Read/report metadata only, without creating SQL or a job.')
        parser.add_argument('--create-job', action='store_true')

    def handle(self, *args, **options):
        source = self.pick(options['connection'], source=True)
        fingerprint = connection_fingerprint(source)
        output = Path(options['output']).resolve()
        if output.exists() and not options['inspect']:
            raise CommandError('SQL-файл уже существует. Укажите новое имя через --output.')
        if options['create_job'] and options['inspect']:
            raise CommandError('--create-job нельзя совмещать с --inspect.')
        try:
            self.stdout.write('[1/3] Читаем регистр графиков и табличные части реестра платежей…')
            roots = read_metadata(source, names=[REGISTER, REGISTRY], include_table_parts=True)
            objects = {obj['name']: obj for obj in roots['objects']}
            follow = set(concrete_references(objects[REGISTER], 'ЗаказПоставщику') + concrete_references(objects[REGISTER], 'Инвойс'))
            self.stdout.write('[2/3] Читаем заказ и инвойс…')
            related = read_metadata(source, names=sorted(follow))
            objects.update({obj['name']: obj for obj in related['objects']})
            if len({(obj.get('configuration'), obj.get('version')) for obj in objects.values()}) != 1:
                raise CommandError('Конфигурация изменилась во время чтения. Повторите подготовку.')
            source.refresh_from_db()
            if fingerprint != connection_fingerprint(source):
                raise CommandError('Подключение изменилось. Повторите подготовку.')
            if options['inspect']:
                for name in (REGISTER,):
                    self.stdout.write(name)
                    for prop in objects[name]['properties']:
                        self.stdout.write('  ' + prop['name'] + (' · ресурс' if prop.get('is_measure') else ' · измерение' if prop.get('is_dimension') else ''))
                self.stdout.write('Табличные части реестра: ' + ', '.join(p['name'] for p in objects[REGISTRY].get('table_parts', [])))
                self.stdout.write('Исходник и задания не изменялись.')
                return
            self.stdout.write('[3/3] Формируем дневные обороты, статусы и готовую сумму управленческого учёта…')
            sql = generate(objects, usd=True, usd_resource=options['usd_resource'])
            with connect(source, timeout=1800) as database:
                columns = describe(database, sql)
            source.refresh_from_db()
            if connection_fingerprint(source) != fingerprint:
                raise CommandError('Подключение изменилось во время проверки. Повторите подготовку.')
            target = self.pick(options['target_connection'], source=False) if options['create_job'] else None
            if target and ETLJob.objects.filter(target_connection=target, target_schema=options['target_schema'], target_table=options['target_table'], is_deleted=False).exists():
                raise CommandError('Задание для этого назначения уже существует. Дубликат не создан.')
            job = None
            with transaction.atomic():
                if target:
                    job = ETLJob(name='График платежей · по дням · USD', source_connection=source, target_connection=target,
                        source_type='SQL_QUERY', sql_query=sql, target_schema=options['target_schema'], target_table=options['target_table'],
                        command_timeout=1800, schedule_enabled=False, description='Вся история и будущие платежи, дневные обороты. Сумма в валюте платежа и готовая сумма управленческого учёта под именем USD. Без курсов, пересчёта и дополнительного округления. Фильтры СКД и права пользователей 1С не воспроизводятся SQL-учётной записью автоматически.')
                    job.full_clean(); job.save()
                    ETLAuditLog.objects.create(object_type='ETLJob', object_id=job.pk, action='create')
                output.parent.mkdir(parents=True, exist_ok=True)
                with output.open('x', encoding='utf-8', newline='\n') as stream:
                    stream.write(sql + '\n')
            self.stdout.write(self.style.SUCCESS(f'SELECT проверен: {len(columns)} столбцов. SQL: {output}'))
            if job:
                self.stdout.write(self.style.SUCCESS(f'Создано задание #{job.pk} → {job.target_schema}.{job.target_table}. Расписание выключено.'))
                self.stdout.write('Откройте «1С Перенос данных → Задания» и выполните первый запуск вручную.')
            self.stdout.write('Перед расписанием сравните суммы и статусы с 1С. USD берётся из готового ресурса управленческого учёта; курсы не используются.')
        except (ValidationError, ETLError) as exc:
            raise CommandError(' '.join(exc.messages) if isinstance(exc, ValidationError) else str(exc)) from None
        except pyodbc.Error:
            raise CommandError('SQL Server не подтвердил запрос. Проверьте структуру объектов и права.') from None
        except (OSError, ValueError, KeyError, TypeError):
            raise CommandError('Не удалось подготовить отчёт. Проверьте доступность объектов 1С, обновление помощника и путь выходного файла.') from None

    @staticmethod
    def pick(name, *, source):
        candidates = DatabaseConnection.objects.filter(is_active=True).exclude(role='TARGET' if source else 'SOURCE')
        if name:
            candidates = candidates.filter(name=name)
        choices = list(candidates[:2])
        if len(choices) != 1:
            option = '--connection' if source else '--target-connection'
            raise CommandError(f'Укажите подключение через {option} "Название из портала".')
        return choices[0]
