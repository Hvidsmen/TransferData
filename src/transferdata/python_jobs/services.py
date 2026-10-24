import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from zoneinfo import ZoneInfo
from pathlib import Path

from apscheduler.triggers.cron import CronTrigger
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from transferdata.web.maintenance import is_paused
from .models import Job, Run, script_path

PREFIX = 'ETL_PROGRESS:'


@transaction.atomic
def enqueue(job, action='run'):
    if is_paused():
        raise ValidationError('Портал обновляется. Повторите после завершения обновления.')
    job = Job.objects.select_for_update().select_related('connection').get(pk=job.pk)
    job.full_clean()
    if job.archived or job.is_deleted:
        raise ValidationError('Задание в архиве или удалено.')
    try:
        with transaction.atomic():
            return Run.objects.create(job=job, snapshot=job.snapshot()), True
    except IntegrityError:
        active = Run.objects.filter(job=job, status__in=['queued', 'running']).first()
        if active is None:
            raise
        return active, False


def schedule_due(now=None):
    if is_paused():
        return
    now = now or timezone.now()
    for job in Job.objects.filter(enabled=True, archived=False, is_deleted=False).select_related('connection'):
        trigger = CronTrigger.from_crontab(job.cron, timezone=ZoneInfo(job.timezone))
        if job.next_run_at is None:
            Job.objects.filter(pk=job.pk, next_run_at=None).update(next_run_at=trigger.get_next_fire_time(None, now))
        elif job.next_run_at <= now:
            with transaction.atomic():
                try:
                    enqueue(job)
                except ValidationError:
                    continue
                Job.objects.filter(pk=job.pk).update(next_run_at=trigger.get_next_fire_time(now, now))


def stop_tree(process, process_job=None):
    if process_job is not None:
        process_job.terminate()
    elif os.name == 'nt':
        # /T includes nested processes started by a script.
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=15)


def execute_next():
    if is_paused():
        return False
    run = Run.objects.filter(status='queued').order_by('created_at', 'pk').first()
    if run is None:
        return False
    if not Run.objects.filter(pk=run.pk, status='queued').update(status='running', started_at=timezone.now()):
        return False
    start = time.monotonic()
    process = None
    reader = None
    process_job = None
    output = deque(maxlen=128)  # Bound in-memory and persisted output, even for a noisy script.
    events = queue.Queue(maxsize=128)
    child_result = None
    child_error = None
    try:
        config = run.snapshot
        builtin = config['handler'] == 'commercial_headers'
        command = [sys.executable, '-u', '-m', 'transferdata.web', 'pythonjobexecute', '--run', str(run.pk)] if builtin else [sys.executable, '-u', str(script_path(config['script']))]
        env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1',
                   TRANSFERDATA_STATE_DIR=str(settings.STATE_DIR), ETL_JOB_PARAMETERS=json.dumps(config['parameters'], ensure_ascii=False))
        process = subprocess.Popen(command, cwd=Path.cwd(), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   start_new_session=os.name != 'nt')
        if os.name == 'nt':
            from .windows_process import ProcessJob
            process_job = ProcessJob(process)
        def read_output():
            try:
                while chunk := process.stdout.readline(4096):
                    # Backpressure bounds memory; the main loop drains this queue throughout execution.
                    events.put(chunk.decode('utf-8', errors='replace').rstrip())
            finally:
                process.stdout.close()
        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        saved = 0
        run.message = 'Обработчик запущен'
        while process.poll() is None or reader.is_alive() or not events.empty():
            if time.monotonic() - start >= config['timeout']:
                stop_tree(process, process_job)
                raise TimeoutError
            try:
                line = events.get(timeout=0.1)
                event = None
                if builtin and line.startswith(PREFIX):
                    try:
                        event = json.loads(line[len(PREFIX):])
                    except ValueError:
                        pass
                if isinstance(event, dict):
                    if isinstance(event.get('error'), str):
                        child_error = event['error'][:2000]
                        run.message = child_error
                        output.append(child_error)
                    elif event.get('result') is True:
                        child_result = event.get('rows', 0)
                    else:
                        run.completed = max(0, int(event.get('completed', run.completed)))
                        total = event.get('total', run.total)
                        run.total = max(0, int(total)) if total is not None else None
                        run.rows = max(0, int(event.get('rows', run.rows)))
                        run.message = str(event.get('message', run.message))[:1000]
                        output.append(run.message)
                else:
                    output.append(line[:4096])
            except queue.Empty:
                pass
            if time.monotonic() - saved >= 1:
                (settings.STATE_DIR / 'python-worker.heartbeat').write_text(timezone.now().isoformat(), encoding='ascii')
                run.output = '\n'.join(output)[-65536:]
                run.save(update_fields=['completed', 'total', 'rows', 'message', 'output'])
                saved = time.monotonic()
        if process.returncode != 0 or (builtin and child_result is None):
            raise ChildProcessError
        run.status = 'success'
        run.message = ('Новых или неполных документов не найдено' if run.total == 0 else 'Обработка завершена') if builtin else 'Python-файл завершён, код возврата 0'
        output.append(run.message)
        if builtin:
            run.rows = child_result
    except Exception as exc:
        run.status = 'failed'
        if isinstance(exc, ValidationError):
            output.append('; '.join(exc.messages))
        run.message = child_error if isinstance(exc, ChildProcessError) and child_error else ('Превышено максимальное время. Обработчик остановлен; проверьте уже выполненные операции.' if isinstance(exc, TimeoutError)
                       else f'Обработка не завершена: {type(exc).__name__}. Подробности в журнале; успешно зафиксированные документы сохранены.')
    finally:
        if process and process.poll() is None:
            stop_tree(process, process_job)
        if process_job is not None:
            process_job.close()
        # Unblock the bounded reader after an error/timeout before joining it.
        if reader:
            while reader.is_alive():
                try:
                    output.append(events.get(timeout=0.1))
                except queue.Empty:
                    pass
            reader.join(timeout=1)
        while not events.empty():
            output.append(events.get_nowait())
        run.output = '\n'.join(output)[-65536:]
        run.seconds = time.monotonic() - start
        run.finished_at = timezone.now()
        run.save(update_fields=['status', 'completed', 'total', 'rows', 'message', 'output', 'seconds', 'finished_at'])
    return True
