from django import template
from django.utils import timezone
from transferdata.web.panel.operations import worker_health

register = template.Library()


@register.inclusion_tag('studio/run_state.html', takes_context=True)
def run_state(context, run, subsystem):
    states = {'PENDING': 'queued', 'RUNNING': 'running', 'SUCCESS': 'success', 'FAILED': 'failed', 'CANCELLED': 'cancelled'}
    state = states.get(run.status, run.status)
    key = {'transferdata': 'sql', 'erp': 'onec', 'python': 'python'}[subsystem]
    health = next(h for h in (context.get('health') or worker_health()) if h['key'] == key)
    phase = getattr(run, 'stage', '') or getattr(run, 'message', '')
    if state == 'queued' and health['state'] == 'paused':
        phase = 'Ожидает завершения обновления портала'
    elif state == 'queued':
        phase = 'В очереди · обработчик занят или ожидает очередного опроса' if health['online'] else 'Ожидает обработчика · нет свежего сигнала'
    elif state == 'running' and not phase:
        phase = 'Выполняется'
    created = run.created_at
    start = getattr(run, 'started_at', None)
    finish = getattr(run, 'finished_at', None)
    elapsed = max(0, int(((start or finish or timezone.now()) - created).total_seconds()))
    wait_label = f'{elapsed // 60} мин {elapsed % 60} с' if elapsed >= 60 else f'{elapsed} с'
    runtime = max(0, int(((finish or timezone.now()) - start).total_seconds())) if start else None
    runtime_label = (f'{runtime // 60} мин {runtime % 60} с' if runtime >= 60 else f'{runtime} с') if runtime is not None else ''
    return {'active': state in ('queued', 'running'), 'state': state, 'phase': phase,
        'wait_seconds': elapsed, 'wait_label': wait_label, 'runtime_label': runtime_label, 'health': health, 'started': start,
        'unknown_total': getattr(run, 'total', None) is None,
        'updated': timezone.now()}


@register.inclusion_tag('studio/failure_help.html')
def failure_help(message):
    value = str(message or '')
    lower = value.lower()
    title = 'Операция не завершилась'
    hint = 'Проверьте настройки задания и подключения. После устранения причины запустите задание повторно.'
    if 'нулевой символ' in lower or 'u+0000' in lower:
        title = 'В тексте обнаружен нулевой символ'
        hint = 'Проверьте указанный столбец. Если допустима очистка U+0000 при переносе, включите её в дополнительных параметрах задания.'
    elif 'курс' in lower:
        title = 'Не удалось определить валютный курс'
        hint = 'Проверьте наличие курса и кратности валюты платежа и USD на дату курса или платежа.'
    elif 'сертификат' in lower or 'ssl' in lower or 'tls' in lower:
        title = 'Не удалось установить защищённое подключение'
        hint = 'Проверьте сертификат SQL Server и настройки TLS подключения.'
    elif 'timeout' in lower or 'таймаут' in lower:
        title = 'Превышено время ожидания'
        hint = 'Проверьте доступность сервера и блокировки. Оцените длительность запроса перед увеличением таймаута.'
    return {'error_title': title, 'error_hint': hint, 'error_message': value}
