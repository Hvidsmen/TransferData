from django.utils import timezone


def portal(request):
    context = {"now": timezone.localtime()}
    if request.path.startswith("/erp/"):
        route = request.resolver_match.url_name if request.resolver_match else "dashboard"
        section = "onec" if route.startswith("onec") else "connections" if route.startswith("connection") else (
            "history" if route.startswith("execution") else "jobs" if route.startswith("job") else "overview")
        context.update(subsystem="erp", section=section,
                       title={"onec": "Метаданные 1С", "connections": "Подключения", "history": "История запусков",
                              "jobs": "Задания ERP → DWH", "overview": "Обзор ERP_to_DWH"}[section])
    return context
