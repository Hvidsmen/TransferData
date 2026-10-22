from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .models import DatabaseConnection, OneCObject
from .onec import enqueue_scan, mapping_from_metadata
from .views import login_required, administrator


@login_required
def catalogue(request):
    connections = DatabaseConnection.objects.filter(is_active=True).exclude(role='TARGET').order_by('name')
    connection = None
    if request.GET.get('connection', '').isdecimal():
        connection = get_object_or_404(connections, pk=request.GET['connection'])
    objects = OneCObject.objects.filter(connection=connection, is_active=True)
    query = request.GET.get('q', '').strip()
    if query:
        objects = objects.filter(name__icontains=query)
    return render(request, 'erp/control/onec_catalogue.html', {'connections': connections, 'connection': connection,
        'query': query, 'page': Paginator(objects, 40).get_page(request.GET.get('page')),
        'scan': connection.onec_scans.first() if connection else None})


@login_required
def detail(request, pk):
    obj = get_object_or_404(OneCObject.objects.select_related('connection'), pk=pk, is_active=True)
    columns = mapping_from_metadata(obj.metadata)['columns'] if obj.metadata else []
    return render(request, 'erp/control/onec_object.html', {'object': obj, 'columns': columns,
        'scan': obj.connection.onec_scans.first()})


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
