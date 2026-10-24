from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_safe
from transferdata.erp.control.models import DatabaseConnection
from .services import read


@staff_member_required(login_url='admin:login')
@require_safe
def jobs(request, job_id=None):
    if not request.user.is_superuser:
        raise PermissionDenied
    connections = list(DatabaseConnection.objects.filter(is_active=True).order_by('name'))
    choice = request.GET.get('connection', '')
    selected = None
    if choice.isdecimal():
        selected = next((item for item in connections if item.pk == int(choice)), None)
        if not selected:
            raise Http404
    elif not choice:
        matching = [item for item in connections if item.server.lower().split('.')[0].split('\\')[0] == 'vm-dwh']
        preferred = [item for item in matching if item.role in ('TARGET', 'BOTH')]
        selected = (preferred or matching)[0] if len(preferred or matching) == 1 else None
    context = {'title': 'Задания SQL Server Agent', 'section': 'agent', 'subsystem': 'agent',
        'connections': connections, 'connection': selected, 'query': request.GET.get('q', '').strip(),
        'status': request.GET.get('status', ''), 'job_id': job_id}
    if selected:
        try:
            data = read(selected, job_id)
        except Exception:
            # Driver messages may include credentials; keep them out of UI and logs.
            context['agent_error'] = True
        else:
            if job_id and not data['job']:
                raise Http404
            context.update(data)
            items = data['jobs']
            context['counts'] = {'total': len(items), 'failed': sum(j.get('last_status') == 0 for j in items),
                'running': sum(j['active'] for j in items), 'disabled': sum(not j['enabled'] for j in items)}
            if context['query']:
                query = context['query'].casefold()
                items = [j for j in items if query in (j['name'] + ' ' + (j['category'] or '')).casefold()]
            if context['status'] == 'disabled':
                items = [j for j in items if not j['enabled']]
            elif context['status'] in ('failed', 'success'):
                code = 0 if context['status'] == 'failed' else 1
                items = [j for j in items if j.get('last_status') == code]
            elif context['status']:
                items = [j for j in items if j['status'] == context['status']]
            context['page'] = Paginator(items, 30).get_page(request.GET.get('page'))
    return render(request, 'sql_agent/detail.html' if job_id else 'sql_agent/jobs.html', context)
