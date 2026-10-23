"""A compact cross-subsystem overview, grouped by shared group and worker."""
from django.urls import reverse
from django.utils import timezone
from .operations import annotate_success, add_facts, worker_health
from .monitoring import annotate_latest, transferdata_jobs, erp_jobs, STATUS_LABELS

WORKERS = [('sql', 'SQL Перенос данных', 'i-transfer'), ('onec', '1С Перенос данных', 'i-db'), ('python', 'Python обработчик', 'i-code')]


def python_jobs(user, ids=None):
    from transferdata.python_jobs.models import Job, Run
    if not user.has_perm('python_jobs.view_job'):
        return []
    jobs = Job.objects.filter(is_deleted=False).select_related('group').order_by('name', 'pk')
    if ids is not None:
        jobs = jobs.filter(pk__in=ids)
    history = user.has_perm('python_jobs.view_run')
    if history:
        jobs = annotate_success(jobs, Run.objects.all(), 'success')
        jobs = annotate_latest(jobs, Run.objects.all(), {'id': 'pk', 'status': 'status', 'started': 'started_at', 'created': 'created_at'})
    return [add_facts({'pk': job.pk, 'name': job.name, 'group_id': job.group_id, 'group': job.group.name if job.group else 'Без группы',
             'status': (job.monitor_status or 'never') if history else 'restricted',
             'url': reverse('python_jobs:edit', args=[job.pk]) if user.has_perm('python_jobs.change_job') else '',
             'run_url': reverse('python_jobs:run', args=[job.monitor_id]) if history and job.monitor_id else '',
             'started': (job.monitor_started or job.monitor_created) if history else None,
             'archived': job.archived, 'scheduled': job.enabled, 'handler': job.get_handler_display()}, job, 'python', history) for job in jobs]


def overview(request):
    query = request.GET.get('q', '').strip()
    worker = request.GET.get('worker', '')
    status = request.GET.get('status', '')
    groups = {}
    total = shown = 0
    attention = []
    health = worker_health()
    request._etl_health = health
    sources = [transferdata_jobs(request.user), erp_jobs(), python_jobs(request.user)]
    for (key, label, icon), jobs in zip(WORKERS, sources):
        total += len(jobs)
        for item in jobs:
            item['worker_name'] = label
            item['label'] = STATUS_LABELS.get(item['status'], item['status'])
            item['waiting_worker'] = item['status'] == 'queued' and health[[w[0] for w in WORKERS].index(key)]['state'] == 'unknown'
            if item['attention'] or item['waiting_worker']:
                attention.append(item)
            if worker and key != worker:
                continue
            if status and not (item['status'] == status or status == 'active' and item['status'] in ('queued', 'running') or status == 'attention' and (item['attention'] or item['waiting_worker'])):
                continue
            if query and query.casefold() not in ' '.join([item['name'], item['group'], label, item.get('handler', '')]).casefold():
                continue
            group = groups.setdefault(item['group_id'], {'id': item['group_id'], 'name': item['group'], 'count': 0, 'workers': {}})
            section = group['workers'].setdefault(key, {'key': key, 'label': label, 'icon': icon, 'jobs': []})
            item['delete_subsystem'] = {'sql': 'transferdata', 'onec': 'erp', 'python': 'python'}[key]
            item['label'] = STATUS_LABELS.get(item['status'], item['status'])
            section['jobs'].append(item)
            group['count'] += 1
            shown += 1
    result = sorted(groups.values(), key=lambda g: (g['id'] is None, g['name'].casefold()))
    for group in result:
        group['workers'] = list(group['workers'].values())
    return {'portal_groups': result, 'job_total': total, 'job_shown': shown, 'query': query,
            'health': health, 'attention_jobs': attention, 'updated_at': timezone.now(),
            'worker': worker, 'status': status, 'workers': WORKERS, 'statuses': STATUS_LABELS.items()}
