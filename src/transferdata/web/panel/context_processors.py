from django.utils import timezone
from .operations import worker_health


def operations(request):
    if not request.user.is_authenticated:
        return {}
    if not hasattr(request, '_etl_health'):
        request._etl_health = worker_health()
    return {'health': request._etl_health, 'now': timezone.localtime()}
