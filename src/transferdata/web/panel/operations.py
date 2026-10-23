"""Observed worker health and shared job facts. Unknown telemetry is never a green status."""
from contextlib import contextmanager
from datetime import datetime, timedelta
import threading
from zoneinfo import ZoneInfo
from apscheduler.triggers.cron import CronTrigger
from django.conf import settings
from django.db.models import OuterRef, Subquery
from django.utils import timezone
from transferdata.web.maintenance import is_paused


@contextmanager
def worker_heartbeat(name):
    path = settings.STATE_DIR / (name + '-worker.heartbeat')
    stop = threading.Event()
    def write():
        try:
            path.write_text(timezone.now().isoformat(), encoding='ascii')
        except OSError:
            pass  # Telemetry must not interrupt a data transaction.
    def loop():
        while not stop.wait(5):
            write()
    write()
    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set(); thread.join(timeout=2)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def worker_health():
    from transferdata.erp.control.models import WorkerLease
    lease = WorkerLease.objects.filter(pk=1).first()
    now = timezone.now()
    result = []
    for key, label, task, filename in [('sql', 'SQL', 'TransferData', 'sql-worker.heartbeat'),
        ('onec', '1С', 'ERP', None), ('python', 'Python', 'Python', 'python-worker.heartbeat')]:
        stamp = lease.heartbeat if filename is None and lease else None
        if filename:
            try:
                stamp = datetime.fromisoformat((settings.STATE_DIR / filename).read_text(encoding='ascii'))
            except (OSError, ValueError):
                pass
        age = (now - stamp).total_seconds() if stamp and timezone.is_aware(stamp) else None
        online = age is not None and -5 <= age <= 60
        paused = is_paused()
        result.append({'key': key, 'name': label, 'task': 'ETL Dev — ' + task,
            'online': online, 'heartbeat': stamp if age is not None else None,
            'state': 'paused' if paused else 'online' if online else 'unknown',
            'label': 'Обновление портала' if paused else 'Работает' if online else 'Нет свежего сигнала',
            'hint': 'Приём заданий приостановлен на время обновления.' if paused else
                'Свежий сигнал обработчика получен.' if online else
                'Проверьте задачу «ETL Dev — ' + task + '» в планировщике Windows.'})
    return result


def annotate_success(jobs, runs, success):
    latest = runs.filter(job_id=OuterRef('pk'), status=success).order_by('-created_at', '-pk')
    return jobs.annotate(last_success_finished=Subquery(latest.values('finished_at')[:1]),
        last_success_created=Subquery(latest.values('created_at')[:1]))


def next_launch(job, subsystem, now=None, consumed=False):
    now = now or timezone.now()
    if job.archived or job.is_deleted:
        return None
    if subsystem != 'erp':
        return job.next_run_at if job.enabled else None
    if not job.schedule_enabled or not job.is_active:
        return None
    cron = job.schedule_cron
    if not cron and job.schedule_time:
        cron = f'{job.schedule_time.minute} {job.schedule_time.hour} * * *'
    try:
        zone = ZoneInfo(settings.TIME_ZONE)
        start = now.astimezone(zone).replace(hour=0, minute=0, second=0, microsecond=0)
        if consumed:
            start += timedelta(days=1)
        return CronTrigger.from_crontab(cron, timezone=zone).get_next_fire_time(None, start)
    except (ValueError, KeyError, TypeError):
        return None


def add_facts(item, job, subsystem, can_history=True, consumed=False):
    now = timezone.now()
    item['last_success'] = (job.last_success_finished or job.last_success_created) if can_history else None
    item['next_run'] = next_launch(job, subsystem, now, consumed)
    item['schedule_label'] = job.schedule_label
    item['overdue'] = bool(item['next_run'] and item['next_run'] < now - timedelta(minutes=1)
        and item['status'] not in ('queued', 'running'))
    item['attention'] = not job.archived and (item['status'] in ('failed', 'interrupted') or item['overdue'])
    item['timezone'] = getattr(job, 'timezone', settings.TIME_ZONE)
    return item


def attach_job_facts(jobs, subsystem, user):
    from .monitoring import transferdata_jobs, erp_jobs, STATUS_LABELS
    from .portal_monitoring import python_jobs
    jobs = list(jobs)
    ids = [j.pk for j in jobs]
    items = (erp_jobs(ids) if subsystem == 'erp' else python_jobs(user, ids) if subsystem == 'python' else transferdata_jobs(user, ids)) if ids else []
    for item in items:
        item['label'] = STATUS_LABELS.get(item['status'], item['status'])
    found = {item['pk']: item for item in items}
    for job in jobs:
        job.ux = found.get(job.pk, {})
    return jobs
