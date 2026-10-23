"""One operational result per job; connection checks are deliberately excluded."""
from django.db.models import OuterRef, Subquery
from django.urls import reverse

from .models import Job, Run

STATUS_LABELS = {'queued': 'В очереди', 'running': 'Выполняется', 'success': 'Успешно',
                 'failed': 'Ошибка', 'interrupted': 'Прерван', 'cancelled': 'Отменён',
                 'never': 'Не запускалось', 'restricted': 'Нет доступа к истории'}


def annotate_latest(jobs, executions, fields):
    latest = executions.filter(job_id=OuterRef('pk')).order_by('-created_at', '-pk')
    return jobs.annotate(**{f'monitor_{name}': Subquery(latest.values(field)[:1]) for name, field in fields.items()})


def transferdata_jobs(user):
    if not user.has_perm('panel.view_job'):
        return []
    jobs = Job.objects.filter(is_deleted=False).select_related('group').order_by('group__name', 'name', 'pk')
    can_view_runs = user.has_perm('panel.view_run')
    if can_view_runs:
        jobs = annotate_latest(jobs, Run.objects.filter(action__in=['run', 'execute']),
            {'id': 'pk', 'status': 'status', 'created': 'created_at', 'started': 'started_at', 'rows': 'rows', 'seconds': 'seconds'})
    result = []
    for job in jobs:
        status = (job.monitor_status or 'never') if can_view_runs else 'restricted'
        result.append({'pk': job.pk, 'name': job.name, 'group_id': job.group_id, 'group': job.group.name if job.group else 'Без группы',
            'archived': job.archived, 'scheduled': job.enabled,
            'status': status, 'label': STATUS_LABELS.get(status, status),
            'url': reverse('td_job_edit', args=[job.pk]) if user.has_perm('panel.change_job') else '',
            'run_url': reverse('td_run', args=[job.monitor_id]) if can_view_runs and job.monitor_id else '',
            'started': (job.monitor_started or job.monitor_created) if can_view_runs else None,
            'rows': job.monitor_rows if can_view_runs else None,
            'seconds': job.monitor_seconds if can_view_runs else None})
    return result


def erp_jobs():
    from transferdata.erp.control.models import ETLJob, ETLExecution
    jobs = ETLJob.objects.filter(is_deleted=False).select_related('group').order_by('group__name', 'name', 'pk')
    jobs = annotate_latest(jobs, ETLExecution.objects.all(),
        {'id': 'pk', 'status': 'status', 'created': 'created_at', 'started': 'started_at',
         'rows': 'rows_loaded', 'seconds': 'duration_seconds'})
    states = {'PENDING': 'queued', 'RUNNING': 'running', 'SUCCESS': 'success', 'FAILED': 'failed', 'CANCELLED': 'cancelled'}
    result = []
    for job in jobs:
        status = states.get(job.monitor_status, 'never')
        result.append({'pk': job.pk, 'name': job.name, 'group_id': job.group_id, 'group': job.group.name if job.group else 'Без группы',
            'archived': job.archived, 'disabled': not job.is_active, 'scheduled': job.schedule_enabled and job.is_active,
            'status': status, 'label': STATUS_LABELS[status], 'url': reverse('erp:job_detail', args=[job.pk]),
            'run_url': reverse('erp:execution_detail', args=[job.monitor_id]) if job.monitor_id else '',
            'started': job.monitor_started or job.monitor_created, 'rows': job.monitor_rows, 'seconds': job.monitor_seconds})
    return result
