"""Idempotent provisioning for the approved YDS SQL tasks. Never calls SQL Server."""
import pyodbc
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from transferdata.erp.control.models import DatabaseConnection
from transferdata.web.panel.models import JobGroup
from transferdata.python_jobs.models import Job
from transferdata.python_jobs.pipeline_examples import definitions, example_steps, provision_key


class Command(BaseCommand):
    help = 'Create the two approved YDS procedure jobs with schedules disabled; preserve all existing jobs.'

    def add_arguments(self, parser):
        parser.add_argument('--connection', help='Use an existing SQL Server connection instead of provisioning MSK-DB2 Windows profiles.')

    @transaction.atomic
    def handle(self, *args, **options):
        if settings.DEPLOYMENT['profile'] != 'yds' and not options['connection']:
            raise CommandError('Вне профиля YDS укажите существующее подключение через --connection.')
        explicit = None
        if options['connection']:
            explicit = DatabaseConnection.objects.filter(name=options['connection'], is_active=True).first()
            if not explicit:
                raise CommandError('Указанное активное подключение не найдено.')
        drivers = pyodbc.drivers()
        driver = next((d for d in ('ODBC Driver 17 for SQL Server', 'ODBC Driver 18 for SQL Server') if d in drivers), 'ODBC Driver 17 for SQL Server')
        for name, database, specifications in definitions():
            # Archived/deleted/operator-edited records are also preserved, never resurrected.
            key = provision_key(database)
            existing = Job.objects.filter(Q(provision_key=key) | Q(name=name)).first()
            if existing:
                if existing.provision_key is None:
                    existing.provision_key = key
                    existing.save(update_fields=['provision_key'])
                self.stdout.write(f'Сохранено существующее задание #{existing.pk}: {existing.name}.')
                continue
            connection = explicit
            if connection is None:
                candidates = list(DatabaseConnection.objects.filter(server__iexact='MSK-DB2', database__iexact=database, is_active=True)[:2])
                if len(candidates) == 1:
                    connection = candidates[0]
                else:
                    profile_name = f'MSK-DB2 · {database} · процедуры'
                    connection, created = DatabaseConnection.objects.get_or_create(name=profile_name, defaults={
                        'server': 'MSK-DB2', 'database': database, 'authentication_type': 'WINDOWS',
                        'driver': driver, 'trust_server_certificate': False, 'role': 'TARGET',
                        'description': 'Служебное подключение для SQL-процедур. Проверьте сервер, базу, TLS и права EXECUTE. Не используется как источник каталога 1С.'})
                    if not created:
                        self.stdout.write(self.style.WARNING(f'Пропущено «{name}»: неоднозначное или изменённое подключение. Создайте задание в браузере с выбранным подключением.'))
                        continue
                    self.stdout.write(f'Создано подключение {profile_name}. Проверьте доступ и сертификат перед первым запуском.')
            group, _ = JobGroup.objects.get_or_create(name='SQL-процедуры')
            job = Job(name=name, provision_key=key, handler='sql_pipeline', connection=connection, group=group,
                transaction_mode='atomic', steps=example_steps(database, specifications), timeout=21600, enabled=False)
            try:
                job.full_clean()
            except ValidationError as exc:
                raise CommandError('; '.join(exc.messages)) from None
            job.save()
            self.stdout.write(self.style.SUCCESS(f'Создано #{job.pk}: {name} · {len(job.steps)} шагов. Расписание выключено.'))
        self.stdout.write('Процедуры не выполнялись. Существующие настройки и расписания не изменялись.')
