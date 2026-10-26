import os
import time
import uuid
import threading
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from transferdata.erp.control.models import WorkerLease, ETLExecution
from django.db import close_old_connections, OperationalError
from transferdata.erp.control.services import claim, execute, recover_interrupted, enqueue_due_jobs
from transferdata.web.maintenance import worker_paused
from transferdata.erp.control.onec import scan_next
from transferdata.erp.control.models import OneCScan

class Command(BaseCommand):
    help = 'Single local worker; OS lock is released automatically on process death.'
    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')
        parser.add_argument('--poll', type=float, default=2)
    def handle(self, *args, **options):
        database = str(settings.DATABASES['default']['NAME'])
        if database.startswith('file:memorydb_') or database == ':memory:':
            import tempfile
            import hashlib
            path = Path(tempfile.gettempdir()) / ('etl-dev-erp-' + hashlib.sha256(database.encode()).hexdigest()[:16] + '.worker.lock')
        else:
            path = Path(database).resolve().with_suffix('.worker.lock')
        with open(path, 'a+b') as lock:
            lock.seek(0)
            lock.write(b'0')
            lock.flush()
            lock.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise CommandError('Другой worker уже запущен для этой служебной БД.')
            recover_interrupted()
            OneCScan.objects.filter(status='RUNNING').update(status='FAILED', finished_at=timezone.now(),
                message='Чтение метаданных прервано остановкой обработчика. Повторите чтение.')
            owner = str(uuid.uuid4())
            self.stdout.write('ERP_to_DWH worker started')
            last_schedule_check = 0
            while True:
                close_old_connections()
                # Report liveness even while maintenance blocks new work.
                WorkerLease.objects.update_or_create(pk=1, defaults={'owner': owner, 'heartbeat': timezone.now()})
                if worker_paused("erp"):
                    if options['once']:
                        return
                    time.sleep(2)
                    continue
                if time.monotonic() - last_schedule_check >= 30:
                    enqueue_due_jobs()
                    last_schedule_check = time.monotonic()
                execution = claim()
                scanned = False
                if execution or OneCScan.objects.filter(status='PENDING').exists():
                    stop = threading.Event()
                    def heartbeat():
                        close_old_connections()
                        try:
                            while not stop.wait(10):
                                try:
                                    now = timezone.now()
                                    if execution:
                                        ETLExecution.objects.filter(pk=execution.pk, status='RUNNING').update(heartbeat=now)
                                    WorkerLease.objects.filter(pk=1, owner=owner).update(heartbeat=now)
                                except OperationalError:
                                    # SQLite may briefly be busy; progress updates also send heartbeats.
                                    pass
                        finally:
                            close_old_connections()
                    thread = threading.Thread(target=heartbeat, daemon=True)
                    thread.start()
                    try:
                        if execution:
                            execute(execution)
                        else:
                            scanned = scan_next()
                    finally:
                        stop.set()
                        thread.join(timeout=35)
                if options['once']:
                    return
                if not execution and not scanned:
                    time.sleep(max(0.1, options['poll']))
