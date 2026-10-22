from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand
from django.utils import timezone
from transferdata.erp.control.models import ETLJob
from transferdata.erp.control.services import enqueue

class Command(BaseCommand):
    help = 'Enqueue today’s due jobs exactly once, using configured TIME_ZONE.'
    def handle(self, *args, **options):
        now = timezone.localtime()
        count = 0
        for job in ETLJob.objects.filter(is_active=True, schedule_enabled=True, load_mode='FULL', schedule_time__lte=now.time().replace(tzinfo=None)):
            try:
                enqueue(job, schedule_key=f'{job.pk}:{now.date().isoformat()}')
                count += 1
            except ValidationError:
                continue
        self.stdout.write(f'Добавлено: {count}')
