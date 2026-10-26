"""Require post-restart telemetry before an updater releases maintenance."""
import time
from datetime import datetime
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from transferdata.web.panel.operations import worker_health


class Command(BaseCommand):
    help = 'Wait for fresh signals from restarted workers; does not start jobs.'

    def add_arguments(self, parser):
        parser.add_argument('--since', required=True)
        parser.add_argument('--worker', action='append', choices=['sql', 'onec', 'python'], required=True)
        parser.add_argument('--timeout', type=int, default=45)

    def handle(self, *args, **options):
        try:
            since = datetime.fromisoformat(options['since'].replace('Z', '+00:00'))
            if not timezone.is_aware(since) or not 1 <= options['timeout'] <= 120:
                raise ValueError()
        except ValueError:
            raise CommandError('Invalid health-check timestamp or timeout.') from None
        deadline = time.monotonic() + options['timeout']
        required = set(options['worker'])
        while True:
            try:
                signals = worker_health()
                pending = required - {item['key'] for item in signals if item['online'] and item['heartbeat'] >= since}
            except Exception:
                # A briefly locked service DB is not a successful health check.
                pending = required
            if not pending:
                self.stdout.write('Fresh post-restart worker signals received: ' + ', '.join(sorted(required)))
                return
            if time.monotonic() >= deadline:
                raise CommandError('No fresh post-restart worker signal: ' + ', '.join(sorted(pending)) + '. Check Windows task state and startup errors; maintenance remains enabled.')
            time.sleep(1)
