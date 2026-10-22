import json
from datetime import timedelta, datetime, time
from functools import wraps
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from functools import partial
login_required = partial(staff_member_required, login_url="admin:login")
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Avg, Sum, Q, OuterRef, Subquery
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from .models import DatabaseConnection, ETLJob, ETLExecution, ETLAuditLog
from .forms import ConnectionForm, JobForm
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
    executions = ETLExecution.objects.select_related('job')
    day = executions.filter(created_at__gte=timezone.now() - timedelta(days=1))
    stats = [('Всего заданий', ETLJob.objects.count()), ('Активных', ETLJob.objects.filter(is_active=True).count()), ('Успешных за сутки', day.filter(status='SUCCESS').count()), ('Ошибок за сутки', day.filter(status='FAILED').count()), ('Выполняется', executions.filter(status='RUNNING').count()), ('Загружено строк', executions.filter(status='SUCCESS').aggregate(v=Sum('rows_loaded'))['v'] or 0), ('Среднее время, с', round(executions.filter(status='SUCCESS').aggregate(v=Avg('duration_seconds'))['v'] or 0, 2))]
    chart = list(executions.filter(status='SUCCESS').order_by('-created_at').values('id', 'duration_seconds')[:30])[::-1]
    return render(request, 'erp/control/dashboard.html', {'stats': stats, 'recent': executions[:10], 'errors': executions.filter(status='FAILED')[:5], 'chart': chart})

@login_required
def connections(request):
    return render(request, 'erp/control/connections.html', {'items': DatabaseConnection.objects.order_by('name')})

@administrator
def edit(request, kind, pk=None):
    model, form_class = (ETLJob, JobForm) if kind == 'job' else (DatabaseConnection, ConnectionForm)
    instance = get_object_or_404(model, pk=pk) if pk else None
    form = form_class(request.POST or None, instance=instance)
    if request.method == 'POST' and form.is_valid():
        obj = form.save(commit=False)
        if kind == 'job' and not obj.created_by_id:
            obj.created_by = request.user
        from django.db import transaction
        with transaction.atomic():
            obj.save()
            ETLAuditLog.objects.create(actor=request.user, object_type=model.__name__, object_id=obj.pk, action='update' if pk else 'create')
        return redirect('erp:job_detail', pk=obj.pk) if kind == 'job' else redirect('erp:connections')
    return render(request, 'erp/control/form.html', {'form': form, 'title': 'ETL-задание' if kind == 'job' else 'Подключение'})

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
    items = ETLJob.objects.select_related('source_connection', 'target_connection')
    group = request.GET.get('group', '')
    if group == '__ungrouped__':
        items = items.filter(group='')
    elif group:
        items = items.filter(group=group)
    if request.GET.get('q'):
        items = items.filter(name__icontains=request.GET['q'])
    if request.GET.get('mode') in ('FULL', 'INCREMENTAL'):
        items = items.filter(load_mode=request.GET['mode'])
    latest = ETLExecution.objects.filter(job=OuterRef('pk')).order_by('-created_at')
    items = items.annotate(last_status=Subquery(latest.values('status')[:1]), last_duration=Subquery(latest.values('duration_seconds')[:1]), last_rows=Subquery(latest.values('rows_loaded')[:1]), last_started=Subquery(latest.values('created_at')[:1]))
    return render(request, 'erp/control/jobs.html', {'items': items.order_by('name'), 'group': group,
        'groups': ETLJob.objects.exclude(group='').order_by('group').values_list('group', flat=True).distinct()})

@login_required
def job_detail(request, pk):
    job = get_object_or_404(ETLJob.objects.select_related('source_connection', 'target_connection'), pk=pk)
    return render(request, 'erp/control/job.html', {'job': job, 'recent': job.executions.all()[:20]})

@administrator
@require_POST
def job_run(request, pk):
    job = get_object_or_404(ETLJob, pk=pk)
    try:
        execution = enqueue(job, request.user)
    except ValidationError as exc:
        messages.error(request, ' '.join(exc.messages))
        return redirect('erp:job_detail', pk=pk)
    return redirect('erp:execution_detail', pk=execution.pk)

@administrator
@require_POST
def job_disable(request, pk):
    job = get_object_or_404(ETLJob, pk=pk)
    from django.db import transaction
    with transaction.atomic():
        job.is_active = False
        job.save(update_fields=['is_active', 'updated_at'])
        ETLAuditLog.objects.create(actor=request.user, object_type='ETLJob', object_id=pk, action='disable')
    return redirect('erp:job_detail', pk=pk)

@login_required
def executions(request):
    items = ETLExecution.objects.select_related('job', 'triggered_by')
    if request.GET.get('status') in dict(ETLExecution.STATUSES):
        items = items.filter(status=request.GET['status'])
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
    return render(request, 'erp/control/executions.html', {'page': Paginator(items, 50).get_page(request.GET.get('page')), 'jobs': ETLJob.objects.all(), 'statuses': ETLExecution.STATUSES})

@login_required
def execution_detail(request, pk):
    execution = get_object_or_404(ETLExecution.objects.select_related('job'), pk=pk)
    template = 'erp/control/progress.html' if request.headers.get('HX-Request') == 'true' else 'erp/control/execution.html'
    return render(request, template, {'execution': execution, 'logs': execution.logs.order_by('-timestamp', '-pk')[:100]})


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
