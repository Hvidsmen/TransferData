from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from transferdata.erp.control.models import DatabaseConnection
from transferdata.python_jobs.models import Job


class Command(BaseCommand):
    help = 'Create the first commercial-proposal headers job without running it or enabling its schedule.'

    def add_arguments(self, parser):
        parser.add_argument('--connection', help='Existing SQL Server connection to DataWH, as named in the portal.')

    def handle(self, *args, **options):
        (settings.STATE_DIR / 'python-scripts').mkdir(parents=True, exist_ok=True)
        existing = Job.objects.filter(handler='commercial_headers', is_deleted=False).first()
        if existing:
            self.stdout.write(f'Задание уже существует: #{existing.pk} {existing.name}. Настройки сохранены.')
            return
        candidates = DatabaseConnection.objects.filter(is_active=True, database__iexact='DataWH')
        if options['connection']:
            candidates = candidates.filter(name=options['connection'])
        else:
            candidates = candidates.filter(server__iexact='vm-dwh')
        choices = list(candidates[:2])
        if len(choices) != 1:
            raise CommandError('Укажите подключение к DataWH: --connection "Название подключения из 1С Перенос данных".')
        job = Job(name='Заголовки коммерческих предложений', connection=choices[0], parameters={'date_from': '2023-01-01'})
        job.full_clean()
        job.save()
        self.stdout.write(self.style.SUCCESS(f'Создано задание #{job.pk}. Расписание выключено, выполнение не запускалось.'))
        self.stdout.write('Откройте «Python обработчик → Задания», проверьте подключение и выполните первый запуск вручную.')
