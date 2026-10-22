from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from datetime import timedelta
from django.utils import timezone
from transferdata.web.maintenance import worker_paused
from .models import DatabaseConnection, OneCObject, WorkerLease, ETLExecution
from .onec import enqueue_scan, mapping_from_metadata
from .views import login_required, administrator


def scan_context(connection):
    scan = connection.onec_scans.first() if connection else None
    warning = ''
    if scan and scan.status == 'PENDING':
        lease = WorkerLease.objects.filter(pk=1).first()
        if worker_paused('erp'):
            warning = 'Обработчик приостановлен для обновления портала. Чтение начнётся после возобновления.'
        elif not lease or not lease.heartbeat or lease.heartbeat < timezone.now() - timedelta(seconds=60):
            warning = 'Нет свежего сигнала ERP-worker. Проверьте, что задача «ETL Dev — ERP» запущена в планировщике Windows.'
        elif ETLExecution.objects.filter(status='RUNNING').exists():
            warning = 'ERP-worker выполняет загрузку. Чтение метаданных начнётся после её завершения.'
        else:
            warning = 'Ожидаем свободный ERP-worker. Чтение базы ещё не началось.'
    return {'scan': scan, 'scan_warning': warning, 'scan_now': timezone.now()}


@login_required
def catalogue(request):
    connections = DatabaseConnection.objects.filter(is_active=True).exclude(role='TARGET').order_by('-created_at', '-pk')
    connection = connections.first()
    if request.GET.get('connection', '').isdecimal():
        connection = get_object_or_404(connections, pk=request.GET['connection'])
    objects = OneCObject.objects.filter(connection=connection, is_active=True)
    kinds = list(objects.values('kind').annotate(count=Count('pk')).order_by('kind'))
    kind = request.GET.get('kind', '').strip()
    if kind:
        objects = objects.filter(kind=kind)
    query = request.GET.get('q', '').strip()
    if query:
        objects = objects.filter(name__icontains=query)
    return render(request, 'erp/control/onec_catalogue.html', {'connections': connections, 'connection': connection,
        'query': query, 'kind': kind, 'kinds': kinds,
        'page': Paginator(objects.order_by('kind', 'name', 'pk'), 40).get_page(request.GET.get('page')),
        **scan_context(connection)})


@login_required
def detail(request, pk):
    obj = get_object_or_404(OneCObject.objects.select_related('connection'), pk=pk, is_active=True)
    mapping = mapping_from_metadata(obj.metadata) if obj.metadata else {}
    columns = mapping.get('columns', [])
    return render(request, 'erp/control/onec_object.html', {'object': obj, 'columns': columns, 'reference_mapping': mapping,
        **scan_context(obj.connection)})


@administrator
@require_POST
def refresh(request, pk):
    connection = get_object_or_404(DatabaseConnection, pk=pk)
    obj = None
    if request.POST.get('object', '').isdecimal():
        obj = get_object_or_404(OneCObject, pk=request.POST['object'], connection=connection, is_active=True)
    try:
        _, created = enqueue_scan(connection, obj)
        messages.success(request, 'Чтение поставлено в очередь ERP-worker.' if created else 'Для подключения уже выполняется чтение метаданных. Дождитесь результата.')
    except ValidationError as exc:
        messages.error(request, ' '.join(exc.messages))
    if obj:
        return redirect('erp:onec_detail', pk=obj.pk)
    return redirect(reverse('erp:onec_catalogue') + f'?connection={connection.pk}')


@login_required
def options(request):
    connection = request.GET.get('connection', '')
    objects = OneCObject.objects.filter(is_active=True, connection__is_active=True).exclude(connection__role='TARGET').exclude(metadata={})
    objects = objects.filter(connection_id=connection) if connection.isdecimal() else objects.none()
    return JsonResponse({'objects': [{'id': obj.pk, 'name': obj.name} for obj in objects]})
