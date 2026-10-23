from datetime import timedelta
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import permission_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q, OuterRef, Subquery
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.conf import settings
from django.views.decorators.http import require_POST
from transferdata.web.maintenance import is_paused
from transferdata.web.panel.grouping import group_jobs
from transferdata.web.panel.models import JobGroup
from .forms import JobForm
from .models import Job, Run
from .services import enqueue


def screen(request, template, **context):
    from datetime import datetime
    try:
        heartbeat = datetime.fromisoformat((settings.STATE_DIR / 'python-worker.heartbeat').read_text(encoding='ascii'))
        context['worker_running'] = timezone.now() - heartbeat < timedelta(seconds=20)
    except (OSError, ValueError, TypeError):
        context['worker_running'] = False
    return render(request, 'python_jobs/' + template, dict(subsystem='python', **context))


def records():
    return Job.objects.filter(is_deleted=False).select_related('connection', 'group').annotate(
        latest_id=Subquery(Run.objects.filter(job=OuterRef('pk')).order_by('-created_at', '-pk').values('pk')[:1]))


def attach_last(jobs):
    last = {r.pk: r for r in Run.objects.filter(pk__in=[j.latest_id for j in jobs if j.latest_id])}
    for job in jobs:
        job.last_run = last.get(job.latest_id)
    return jobs


@staff_member_required(login_url='admin:login')
@permission_required('python_jobs.view_job', raise_exception=True)
def overview(request):
    jobs = attach_last(list(records().filter(archived=False).order_by('name')))
    return screen(request, 'overview.html', title='Обзор', section='overview', jobs=jobs,
                  counts={'jobs': len(jobs), 'running': Run.objects.filter(status='running').count(),
                          'queued': Run.objects.filter(status='queued').count(),
                          'failed': sum(j.last_run is not None and j.last_run.status in ('failed', 'interrupted') for j in jobs)})


@staff_member_required(login_url='admin:login')
@permission_required('python_jobs.view_job', raise_exception=True)
def jobs(request):
    query = request.GET.get('q', '').strip()
    group = request.GET.get('group', '')
    state = request.GET.get('state', 'active')
    items = records().filter(archived=state == 'archived')
    if query:
        items = items.filter(Q(name__icontains=query) | Q(script__icontains=query))
    if group == '__ungrouped__':
        items = items.filter(group__isnull=True)
    elif group.isdecimal():
        items = items.filter(group_id=int(group))
    page, sections, current = group_jobs(items, request.GET.get('page'))
    attach_last(current)
    return screen(request, 'jobs.html', title='Задания', section='jobs', job_sections=sections, groups=JobGroup.objects.all(),
                  query=query, group=group, state=state, page=page)


@staff_member_required(login_url='admin:login')
def edit(request, pk=None):
    from django.core.exceptions import PermissionDenied
    if not request.user.has_perm('python_jobs.change_job' if pk else 'python_jobs.add_job'):
        raise PermissionDenied
    job = get_object_or_404(Job, pk=pk, is_deleted=False) if pk else None
    form = JobForm(request.POST or None, instance=job)
    if request.method == 'POST' and form.is_valid():
        if is_paused():
            form.add_error(None, 'Портал обновляется. Сохраните после завершения обновления.')
        elif job and Run.objects.filter(job=job, status__in=['running', 'queued']).exists():
            form.add_error(None, 'Дождитесь завершения активного запуска перед изменением задания.')
        else:
            saved = form.save(commit=False)
            if set(form.changed_data) & {'cron', 'timezone', 'enabled'}:
                saved.next_run_at = None
            saved.save()
            messages.success(request, 'Задание сохранено.')
            return redirect('python_jobs:jobs')
    return screen(request, 'edit.html', title='Настройка задания' if pk else 'Новое Python-задание', section='jobs', form=form)


@staff_member_required(login_url='admin:login')
@permission_required('python_jobs.change_job', raise_exception=True)
@require_POST
def run_job(request, pk):
    job = get_object_or_404(Job, pk=pk, is_deleted=False)
    try:
        run, created = enqueue(job)
        messages.info(request, 'Задание поставлено в очередь.' if created else 'У задания уже есть активный запуск.')
        return redirect('python_jobs:run', pk=run.pk)
    except ValidationError as exc:
        messages.error(request, '; '.join(exc.messages))
        return redirect('python_jobs:jobs')


@staff_member_required(login_url='admin:login')
@permission_required('python_jobs.view_run', raise_exception=True)
def history(request):
    items = Run.objects.select_related('job')
    query = request.GET.get('q', '').strip()
    status = request.GET.get('status', '')
    if query:
        items = items.filter(job__name__icontains=query)
    if status in dict(Run.STATUS):
        items = items.filter(status=status)
    return screen(request, 'history.html', title='История запусков', section='history', query=query, status=status,
                  statuses=Run.STATUS, page=Paginator(items, 30).get_page(request.GET.get('page')))


@staff_member_required(login_url='admin:login')
@permission_required('python_jobs.view_run', raise_exception=True)
def detail(request, pk):
    run = get_object_or_404(Run.objects.select_related('job'), pk=pk)
    return screen(request, 'run.html', title=run.job.name, section='history', run=run)
