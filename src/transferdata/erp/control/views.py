import json
from datetime import timedelta, datetime, time
from functools import wraps
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from functools import partial
login_required = partial(staff_member_required, login_url="admin:login")
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Sum, Q, OuterRef, Subquery, Count
from django.db.models.functions import TruncDate
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.urls import reverse
from django.views.decorators.http import require_POST
from .models import DatabaseConnection, ETLJob, ETLExecution, ETLAuditLog
from .forms import ConnectionForm, JobForm
from transferdata.web.panel.models import JobGroup
from transferdata.web.panel.grouping import group_jobs
from .services import enqueue
from transferdata.erp.engine.connections import test_connection
from transferdata.erp.engine.exceptions import ETLError

def administrator(view):
    @wraps(view)
    @login_required
    def wrapped(request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapped

@login_required
def dashboard(request):
    from transferdata.web.panel.monitoring import erp_jobs
    executions = ETLExecution.objects.select_related('job')
    stats = executions.aggregate(successful=Count('pk', filter=Q(status='SUCCESS')),
        running=Count('pk', filter=Q(status='RUNNING')), queued=Count('pk', filter=Q(status='PENDING')),
        failed=Count('pk', filter=Q(status='FAILED')), rows=Sum('rows_loaded', filter=Q(status='SUCCESS')))
    today = timezone.localdate()
    daily = {row['day']: row['count'] for row in executions.filter(created_at__date__gte=today - timedelta(days=6))
        .annotate(day=TruncDate('created_at')).values('day').annotate(count=Count('pk'))}
    peak = max(daily.values(), default=1) or 1
    activity = [{'day': today - timedelta(days=offset), 'count': daily.get(today - timedelta(days=offset), 0),
                 'height': round(100 * daily.get(today - timedelta(days=offset), 0) / peak)} for offset in range(6, -1, -1)]
    chart = list(executions.filter(status='SUCCESS').order_by('-created_at').values('id', 'duration_seconds')[:30])[::-1]
    return render(request, 'erp/control/dashboard.html', {'stats': stats, 'recent': executions[:6],
        'errors': executions.filter(status='FAILED')[:5], 'chart': chart, 'monitor_jobs': erp_jobs(), 'activity': activity})

@login_required
def connections(request):
    return render(request, 'erp/control/connections.html', {'items': DatabaseConnection.objects.order_by('name')})

@administrator
def edit(request, kind, pk=None):
    model, form_class = (ETLJob, JobForm) if kind == 'job' else (DatabaseConnection, ConnectionForm)
    instance = get_object_or_404(model.objects.filter(is_deleted=False) if kind == 'job' else model, pk=pk) if pk else None
    initial = {}
    if kind == 'job' and pk is None and request.GET.get('onec', '').isdecimal():
        from .models import OneCObject
        obj = get_object_or_404(OneCObject, pk=request.GET['onec'], is_active=True)
        initial = {'source_type': 'ONEC', 'source_connection': obj.connection_id, 'one_c_object': obj.pk}
    form = form_class(request.POST or None, instance=instance, initial=initial)
    if request.method == 'POST' and form.is_valid():
        obj = form.save(commit=False)
        if kind == 'job' and not obj.created_by_id:
            obj.created_by = request.user
        from django.db import transaction
        with transaction.atomic():
            obj.save()
            ETLAuditLog.objects.create(actor=request.user, object_type=model.__name__, object_id=obj.pk, action='update' if pk else 'create')
        return redirect('erp:job_detail', pk=obj.pk) if kind == 'job' else redirect('erp:connections')
    return render(request, 'erp/control/form.html', {'form': form, 'kind': kind, 'title': ('Настройка' if pk else 'Создание') + (' задания' if kind == 'job' else ' подключения')})

@administrator
@require_POST
def connection_test(request, pk):
    obj = get_object_or_404(DatabaseConnection, pk=pk)
    try:
        test_connection(obj)
        messages.success(request, 'Подключение успешно: SELECT 1 выполнен.')
    except ETLError as exc:
        messages.error(request, str(exc))
    return redirect('erp:connections')

@login_required
def jobs(request):
    state = request.GET.get('state', 'active')
    items = ETLJob.objects.filter(is_deleted=False, archived=state == 'archived').select_related('source_connection', 'target_connection', 'group', 'one_c_object')
    group = request.GET.get('group', '')
    if group == '__ungrouped__':
        items = items.filter(group__isnull=True)
    elif group.isdecimal():
        items = items.filter(group_id=int(group))
    if request.GET.get('q'):
        items = items.filter(name__icontains=request.GET['q'])
    if request.GET.get('mode') in ('FULL', 'INCREMENTAL'):
        items = items.filter(load_mode=request.GET['mode'])
    latest = ETLExecution.objects.filter(job=OuterRef('pk')).order_by('-created_at')
    items = items.annotate(last_status=Subquery(latest.values('status')[:1]), last_duration=Subquery(latest.values('duration_seconds')[:1]), last_rows=Subquery(latest.values('rows_loaded')[:1]), last_started=Subquery(latest.values('created_at')[:1]))
    page, sections, jobs_on_page = group_jobs(items, request.GET.get('page'))
    from transferdata.web.panel.operations import attach_job_facts
    attach_job_facts(jobs_on_page, "erp", request.user)
    return render(request, 'erp/control/jobs.html', {'items': jobs_on_page, 'job_sections': sections, 'page': page, 'group': group, 'state': state,
        'groups': JobGroup.objects.all()})

@login_required
def job_detail(request, pk):
    job = get_object_or_404(ETLJob.objects.select_related('source_connection', 'target_connection'), pk=pk)
    from transferdata.erp.engine.extractor import source_sql
    return render(request, 'erp/control/job.html', {'job': job, 'recent': job.executions.all()[:20],
        'generated_sql': source_sql(job) if job.source_type == 'ONEC' and job.one_c_mapping else ''})

@administrator
@require_POST
def job_run(request, pk):
    job = get_object_or_404(ETLJob, pk=pk, is_deleted=False)
    try:
        execution = enqueue(job, request.user)
    except ValidationError as exc:
        active = ETLExecution.objects.filter(job=job, status__in=['PENDING', 'RUNNING']).first()
        if active:
            messages.info(request, 'У задания уже есть активный запуск.')
            return redirect('erp:execution_detail', pk=active.pk)
        messages.error(request, ' '.join(exc.messages))
        return redirect('erp:job_detail', pk=pk)
    return redirect('erp:execution_detail', pk=execution.pk)

@administrator
@require_POST
def job_disable(request, pk):
    job = get_object_or_404(ETLJob, pk=pk, is_deleted=False)
    from django.db import transaction
    with transaction.atomic():
        job.is_active = False
        job.save(update_fields=['is_active', 'updated_at'])
        ETLAuditLog.objects.create(actor=request.user, object_type='ETLJob', object_id=pk, action='disable')
    return redirect('erp:job_detail', pk=pk)

@login_required
def executions(request):
    items = ETLExecution.objects.select_related('job', 'triggered_by')
    status = request.GET.get('status', '').upper()
    if status == 'QUEUED':
        status = 'PENDING'
    if status in dict(ETLExecution.STATUSES):
        items = items.filter(status=status)
    if request.GET.get('job', '').isdigit():
        items = items.filter(job_id=request.GET['job'])
    if request.GET.get('trigger') in ('MANUAL', 'SCHEDULED'):
        items = items.filter(trigger_type=request.GET['trigger'])
    try:
        for key, lookup in [('from', 'created_at__gte'), ('to', 'created_at__lt')]:
            if request.GET.get(key):
                date = datetime.strptime(request.GET[key], '%Y-%m-%d').date()
                if key == 'to': date += timedelta(days=1)
                items = items.filter(**{lookup: timezone.make_aware(datetime.combine(date, time.min))})
    except ValueError:
        return HttpResponseBadRequest('Некорректная дата')
    from django.core.paginator import Paginator
    return render(request, 'erp/control/executions.html', {'page': Paginator(items, 50).get_page(request.GET.get('page')), 'jobs': ETLJob.objects.all(), 'statuses': ETLExecution.STATUSES, 'selected_status': status})

@login_required
def execution_detail(request, pk):
    execution = get_object_or_404(ETLExecution.objects.select_related('job'), pk=pk)
    template = 'erp/control/progress.html' if request.headers.get('HX-Request') == 'true' else 'erp/control/execution.html'
    active = ETLExecution.objects.filter(job=execution.job, status__in=['PENDING', 'RUNNING']).first()
    return render(request, template, {'execution': execution, 'logs': execution.logs.order_by('-timestamp', '-pk')[:100],
        'retry_allowed': request.user.is_superuser and execution.job.is_active and not execution.job.archived and not execution.job.is_deleted,
        'retry_url': reverse('erp:job_run', args=[execution.job_id]),
        'active_run_url': reverse('erp:execution_detail', args=[active.pk]) if active else ''})


@administrator
@require_POST
def execution_cancel(request, pk):
    from .services import cancel_pending
    execution = get_object_or_404(ETLExecution, pk=pk)
    if cancel_pending(execution):
        messages.success(request, 'Ожидающий запуск отменён.')
    else:
        messages.error(request, 'Можно отменить только ожидающий запуск. Выполняющийся перенос прерывается остановкой worker с последующей проверкой назначения.')
    return redirect('erp:execution_detail', pk=pk)
