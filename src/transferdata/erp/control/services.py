from datetime import timedelta
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from .models import ETLExecution, ETLExecutionLog
from transferdata.erp.engine.exceptions import ETLError
from transferdata.erp.engine.runner import run

@transaction.atomic
def enqueue(job, user=None, schedule_key=None):
    from transferdata.web.maintenance import is_paused
    if is_paused():
        raise ValidationError('Портал обновляется. Повторите запуск позже.')
    job.runnable()
    job = type(job).objects.select_for_update().select_related('source_connection', 'target_connection').get(pk=job.pk)
    job.runnable()
    try:
        with transaction.atomic():
            return ETLExecution.objects.create(job=job, triggered_by=user, trigger_type='SCHEDULED' if schedule_key else 'MANUAL', schedule_key=schedule_key)
    except IntegrityError:
        raise ValidationError('Задание уже в очереди/выполняется либо плановый запуск уже создан.') from None

def claim():
    # Conditional write is the DB-level compare-and-swap; Python checks are not locks.
    for pk in ETLExecution.objects.filter(status='PENDING').order_by('created_at').values_list('pk', flat=True):
        now = timezone.now()
        if ETLExecution.objects.filter(pk=pk, status='PENDING').update(status='RUNNING', started_at=now, heartbeat=now, stage='Подключение') == 1:
            return ETLExecution.objects.select_related('job__source_connection', 'job__target_connection').get(pk=pk)
    return None

def finish(execution, status, error=''):
    now = timezone.now()
    duration = (now - execution.started_at).total_seconds()
    changed = ETLExecution.objects.filter(pk=execution.pk, status='RUNNING').update(status=status, finished_at=now, duration_seconds=duration, error_message=error, stage=status, heartbeat=now)
    if changed:
        ETLExecutionLog.objects.create(execution=execution, level='ERROR' if error else 'INFO', message=error or 'Перенос завершён.')

def execute(execution):
    def progress(count, stage):
        ETLExecution.objects.filter(pk=execution.pk, status='RUNNING').update(rows_extracted=count, rows_loaded=count, stage=stage, heartbeat=timezone.now())
        ETLExecutionLog.objects.create(execution=execution, level='INFO', message=stage, rows_processed=count)
    try:
        run(execution.job, progress)
    except Exception as exc:
        # Raw driver errors may expose secrets or SQL literals. Record safe category only.
        message = str(exc) if isinstance(exc, ETLError) else 'Ошибка переноса (' + type(exc).__name__ + '). Проверьте конфигурацию, права, SQL и доступность серверов.'
        finish(execution, 'FAILED', message)
    else:
        finish(execution, 'SUCCESS')

def recover_interrupted():
    # Only called after obtaining the exclusive local worker OS lock. No live worker
    # can still publish. Never automatically retry an ambiguous publication.
    for execution in ETLExecution.objects.filter(status='RUNNING'):
        finish(execution, 'FAILED', 'Worker прерван. Проверьте назначение и оставшиеся _etl_ staging-таблицы перед повторным запуском; публикация могла завершиться.')


def cancel_pending(execution):
    return ETLExecution.objects.filter(pk=execution.pk, status='PENDING').update(status='CANCELLED', stage='Отменено до начала', finished_at=timezone.now()) == 1


def enqueue_due_jobs():
    from .models import ETLJob
    from transferdata.scheduling import due_today
    now = timezone.localtime()
    count = 0
    for job in ETLJob.objects.filter(is_active=True, schedule_enabled=True, load_mode='FULL', archived=False, is_deleted=False):
        if job.schedule_cron:
            try:
                if not due_today(job.schedule_cron, now):
                    continue
            except ValueError:
                continue
        elif job.schedule_time is None or job.schedule_time > now.time().replace(tzinfo=None):
            continue
        try:
            enqueue(job, schedule_key=f'{job.pk}:{now.date().isoformat()}')
            count += 1
        except ValidationError:
            continue
    return count
