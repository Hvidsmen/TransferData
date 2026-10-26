import json
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone
from transferdata.erp.control.models import WorkerLease


class Command(BaseCommand):
    help = 'Show ERP heartbeat and recent safe lifecycle events.'

    def handle(self, *args, **options):
        lease = WorkerLease.objects.filter(pk=1).first()
        self.stdout.write('Maintenance: ' + str((settings.STATE_DIR / 'maintenance.flag').exists()))
        self.stdout.write('Heartbeat age (seconds): ' + (str(round((timezone.now()-lease.heartbeat).total_seconds())) if lease and lease.heartbeat else 'missing'))
        path = settings.STATE_DIR / 'erp-worker-events.jsonl'
        if not path.exists():
            self.stdout.write('No lifecycle events yet. Install the updated ERP worker first.')
            return
        with path.open('rb') as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - 8192))
            if size > 8192:
                stream.readline()
            lines = stream.read().decode('utf-8', errors='replace').splitlines()[-15:]
        for line in lines:
            try:
                event = json.loads(line)
                self.stdout.write(' | '.join(str(event.get(k, '')) for k in ('at', 'event', 'phase', 'error_type')))
            except ValueError:
                continue
