import time
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from django.utils import timezone
from transferdata.cli import instance_lock
from transferdata.web.maintenance import worker_paused
from transferdata.python_jobs.models import Run
from transferdata.python_jobs.services import schedule_due, execute_next


class Command(BaseCommand):
    help = 'Run the Python queue and schedules; one worker per portal.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')

    def handle(self, *args, **options):
        try:
            with instance_lock(settings.STATE_DIR / 'python-worker'):
                Run.objects.filter(status='running').update(status='interrupted', finished_at=timezone.now(), message='Обработчик остановлен до записи результата. Проверьте данные перед повторным запуском.')
                self.stdout.write('Python worker started')
                try:
                    while True:
                        close_old_connections()
                        (settings.STATE_DIR / 'python-worker.heartbeat').write_text(timezone.now().isoformat(), encoding='ascii')
                        if worker_paused('python'):
                            if options['once']:
                                return
                            time.sleep(2)
                            continue
                        schedule_due()
                        worked = execute_next()
                        if options['once']:
                            return
                        if not worked:
                            time.sleep(2)
                finally:
                    (settings.STATE_DIR / 'python-worker.heartbeat').unlink(missing_ok=True)
        except RuntimeError as exc:
            raise CommandError(str(exc)) from None
