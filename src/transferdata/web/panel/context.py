from django.utils import timezone
from django.conf import settings


def portal(request):
    context = {"now": timezone.localtime(), "portal_name": settings.PORTAL_NAME, "portal_task_prefix": settings.PORTAL_TASK_PREFIX,
               "sql_enabled": 'sql' in settings.PORTAL_WORKERS, "agent_enabled": 'agent' in settings.PORTAL_WORKERS,
               "portal_description": '1С и Python' if 'sql' not in settings.PORTAL_WORKERS else 'SQL, 1С и Python'}
    if request.path.startswith("/erp/"):
        route = request.resolver_match.url_name if request.resolver_match else "dashboard"
        section = "onec" if route.startswith("onec") else "connections" if route.startswith("connection") else (
            "history" if route.startswith("execution") else "jobs" if route.startswith("job") else "overview")
        context.update(subsystem="erp", section=section,
                       title={"onec": "Метаданные 1С", "connections": "Подключения", "history": "История запусков",
                              "jobs": "Задания", "overview": "Обзор"}[section])
    return context
