from django import template

register = template.Library()


@register.filter
def status_label(value):
    return {'PENDING': 'В очереди', 'RUNNING': 'Выполняется', 'SUCCESS': 'Успешно',
            'FAILED': 'Ошибка', 'CANCELLED': 'Отменён'}.get(value, 'Не запускалось')


@register.filter
def status_class(value):
    return {'PENDING': 'queued', 'RUNNING': 'running', 'SUCCESS': 'success',
            'FAILED': 'failed', 'CANCELLED': 'cancelled'}.get(value, 'never')


@register.filter
def role_label(value):
    return {'SOURCE': 'Источник', 'TARGET': 'Назначение', 'BOTH': 'Источник и назначение'}.get(value, value)
