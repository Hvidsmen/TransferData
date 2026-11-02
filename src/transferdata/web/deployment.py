"""Non-secret installation profile. Shared code, independent state per deployment."""
import json
from django.core.exceptions import ImproperlyConfigured


def load(state_dir):
    path = state_dir / 'deployment.json'
    if not path.exists():
        return {'profile': 'default', 'name': 'ETL Dev', 'task_prefix': 'ETL Dev',
                'workers': ('sql', 'onec', 'python', 'agent'), 'host': None}
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        if data != {'version': 1, 'profile': 'yds'}:
            raise ValueError()
    except (OSError, ValueError, TypeError):
        raise ImproperlyConfigured('Invalid deployment.json. Expected version 1, profile yds.') from None
    return {'profile': 'yds', 'name': 'etl-dev-yds', 'task_prefix': 'etl-dev-yds',
            'workers': ('onec', 'python'), 'host': '192.168.70.9'}


class EnabledSections:
    """Reject disabled features, including direct URLs; not a replacement for permissions."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.conf import settings
        from django.http import HttpResponseNotFound
        path = request.path
        sql_paths = ('/transferdata/', '/jobs/', '/connections/', '/runs/', '/panel/job/', '/panel/run/', '/panel/connection/')
        if ('sql' not in settings.PORTAL_WORKERS and path.startswith(sql_paths)
                or 'agent' not in settings.PORTAL_WORKERS and path.startswith('/sql-agent/')):
            return HttpResponseNotFound('Раздел отключён в этой установке.')
        return self.get_response(request)
