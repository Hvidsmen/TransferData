import time
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone
from transferdata.cli import instance_lock
from transferdata.web.panel.models import Run
from transferdata.web.panel.services import execute_next, schedule_due


class Command(BaseCommand):
    help = "Execute queued ETL jobs and persisted cron schedules; one worker per state directory."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="One scheduling/queue cycle, for diagnosis")

    def handle(self, *args, **options):
        with instance_lock(settings.STATE_DIR):
            # We own the sole worker lock. A previous process may have died
            # after committing external data but before updating the history.
            Run.objects.filter(status="running").update(status="interrupted", finished_at=timezone.now(),
                message="Worker остановлен до записи результата. Проверьте цель перед повторным запуском.")
            self.stdout.write("Worker started")
            try:
                while True:
                    close_old_connections()
                    schedule_due()
                    worked = execute_next()
                    if options["once"]:
                        return
                    if not worked:
                        time.sleep(2)
            except KeyboardInterrupt:
                self.stdout.write("Worker stopped")
