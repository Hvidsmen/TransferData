from uuid import UUID
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_GET
from . import service


def administrator(request):
    if not request.user.is_superuser:
        raise PermissionDenied


@staff_member_required(login_url='admin:login')
@never_cache
@require_http_methods(['GET','POST'])
def page(request):
    administrator(request)
    if request.method == 'POST':
        if request.POST.get('confirmed') != 'yes':
            messages.error(request, 'Подтвердите ожидание заданий и перезапуск портала.')
        else:
            try:
                run_id = service.enqueue(request.user)
                return redirect(reverse('portal_update') + '?run=' + run_id)
            except service.UpdateError as exc:
                messages.error(request, str(exc))
        return redirect('portal_update')
    current = service.latest()
    if request.GET.get('run'):
        try:
            current = service.status(UUID(request.GET['run']))
        except ValueError:
            raise Http404 from None
    return render(request, 'portal_update/page.html', {'title':'Обновление портала','subsystem':None,
        'section':'update','updater_configured':service.configured(),'update_state':current,
        'update_status_url':reverse('portal_update_status', args=[UUID(current['id'])]) if current else ''})


@staff_member_required(login_url='admin:login')
@never_cache
@require_GET
def status(request, run_id):
    administrator(request)
    data = service.status(run_id)
    if data is None:
        raise Http404
    return JsonResponse(data)
