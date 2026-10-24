"""Cached Agent cards for the home page. External SQL never blocks its HTTP request."""
from hashlib import sha256
from threading import Lock, Thread
from urllib.parse import urlencode
from django.core.cache import cache
from django.db import close_old_connections
from django.urls import reverse
from django.utils import timezone
from . import services

_guard = Lock()
_refreshing = False


def choose_connection(connections, saved=None):
    if saved:
        selected = next((item for item in connections if str(item.pk) == str(saved)), None)
        if selected:
            return selected
    matching = [item for item in connections if item.server.strip().lower().split('.')[0].split('\\')[0] == 'vm-dwh']
    preferred = [item for item in matching if item.role in ('TARGET', 'BOTH')]
    candidates = preferred or matching
    return candidates[0] if len(candidates) == 1 else None


def _refresh(config, snapshot_key, error_key):
    global _refreshing
    try:
        close_old_connections()
        data = services.read(config)
        cache.set(snapshot_key, data, 3600)
        cache.delete(error_key)
    except Exception:
        # Never persist raw driver errors or credentials in the overview.
        cache.set(error_key, True, 30)
    finally:
        try:
            close_old_connections()
        finally:
            with _guard:
                _refreshing = False


def schedule_refresh(config, snapshot_key, error_key):
    global _refreshing
    with _guard:
        if _refreshing:
            return
        _refreshing = True
    try:
        Thread(target=_refresh, args=(config, snapshot_key, error_key), daemon=True).start()
    except Exception:
        with _guard:
            _refreshing = False
        cache.set(error_key, True, 30)


def cards(request):
    if not request.user.is_superuser:
        return [], None
    from transferdata.erp.control.models import DatabaseConnection
    connections = list(DatabaseConnection.objects.filter(is_active=True).order_by('name'))
    config = choose_connection(connections, request.session.get('sql_agent_connection'))
    if config is None:
        return [], {'state': 'selection', 'message': 'Выберите подключение в разделе SQL Server Agent, чтобы показывать его задания на главной.'}
    base = services.key(config)
    snapshot_key, error_key = base + ':overview', base + ':overview-error'
    direct = cache.get(base)
    data = direct or cache.get(snapshot_key)
    if direct is not None:
        cache.delete(error_key)
    error = bool(cache.get(error_key))
    age = (timezone.now() - data['observed_at']).total_seconds() if data else None
    if (age is None or age >= 15) and not error:
        schedule_refresh(config, snapshot_key, error_key)
    if data:
        # Keep the last good snapshot visible while the next read is pending.
        cache.set(snapshot_key, data, 3600)
    state = 'error' if error else 'loading' if not data else 'stale' if age >= 30 else 'ready'
    notice = {'state': state, 'server': config.server, 'observed_at': data['observed_at'] if data else None,
        'message': {'error':'Не удалось обновить SQL Server Agent. Проверьте подключение и права чтения msdb.',
            'loading':'Получаем задания SQL Server Agent. Они появятся автоматически.',
            'stale':'Обновляем сведения SQL Server Agent. Показан предыдущий снимок.',
            'ready':'Задания SQL Server Agent включены в общий обзор.'}[state]}
    if not data:
        return [], notice
    result = []
    for job in data['jobs']:
        category = job.get('category') or 'Без категории'
        detail = reverse('sql_agent_detail', args=[job['id']]) + '?' + urlencode({'connection':config.pk})
        plans = [p for p in job.get('schedules', []) if p['enabled']]
        result.append({'pk': job['id'], 'name':job['name'], 'source':'agent',
            'group_id':'agent-' + sha256(category.encode()).hexdigest()[:16], 'group':'SQL Agent · ' + category,
            'status':job['status'], 'label':job['label'], 'url':detail, 'run_url':detail,
            'started':job.get('last_run'), 'archived':False, 'disabled':not job['enabled'],
            'scheduled':bool(job['enabled'] and plans), 'handler':config.server,
            'next_run':job.get('next_run') if job['enabled'] else None,
            'last_success':job.get('last_success') or (job.get('last_run') if job.get('last_status') == 1 else None),
            'attention':job.get('last_status') == 0, 'attention_reason':'Последний итог — ошибка' if job.get('last_status') == 0 else '', 'overdue':False,
            'snapshot_at':data['observed_at'], 'stale':state != 'ready'})
    return result, notice
