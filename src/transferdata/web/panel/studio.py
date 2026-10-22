from datetime import timedelta

from django import forms
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import permission_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import JobForm
from .models import Connection, Job, JobGroup, Run
from .services import enqueue


def legacy_jobs(request):
    if request.method == "GET":
        return redirect("td_jobs")
    # Preserve old action form POSTs and in-flight confirmation pages.
    from .admin import site
    return site.admin_view(site._registry[Job].changelist_view)(request)


def screen(request, template, **context):
    return render(request, "studio/" + template, {"now": timezone.localtime(), "subsystem": "transferdata", **context})


@staff_member_required(login_url="admin:login")
def portal(request):
    return screen(request, "portal.html", section="portal", subsystem=None, title="Портал ETL Dev")


@staff_member_required(login_url="admin:login")
def overview(request):
    context = {"section": "overview", "title": "Обзор"}
    if request.user.has_perm("panel.view_run"):
        runs = Run.objects.all()
        context["stats"] = runs.aggregate(
            successful=Count("pk", filter=Q(status="success")),
            running=Count("pk", filter=Q(status="running")),
            queued=Count("pk", filter=Q(status="queued")),
            failed=Count("pk", filter=Q(status="failed")),
            rows=Sum("rows", filter=Q(status="success", action="run")))
        context["recent"] = runs.select_related("job")[:6]
        today = timezone.localdate()
        daily = {row["day"]: row["count"] for row in runs.filter(created_at__date__gte=today - timedelta(days=6))
                 .annotate(day=TruncDate("created_at")).values("day").annotate(count=Count("pk"))}
        peak = max(daily.values(), default=1) or 1
        context["activity"] = [{"day": today - timedelta(days=offset),
                                "count": daily.get(today - timedelta(days=offset), 0),
                                "height": round(100 * daily.get(today - timedelta(days=offset), 0) / peak)}
                               for offset in range(6, -1, -1)]
    if request.user.has_perm("panel.view_job"):
        context["jobs"] = Job.objects.filter(is_deleted=False, archived=False).select_related("connection", "group").order_by("name")[:3]
        context["job_count"] = Job.objects.filter(is_deleted=False, archived=False).count()
    return screen(request, "overview.html", **context)


@staff_member_required(login_url="admin:login")
@permission_required("panel.view_run", raise_exception=True)
def history(request):
    runs = Run.objects.select_related("job")
    status = request.GET.get("status", "")
    query = request.GET.get("q", "").strip()
    if status in dict(Run.STATUS):
        runs = runs.filter(status=status)
    if query:
        runs = runs.filter(job__name__icontains=query)
    return screen(request, "history.html", section="history", title="История запусков", status=status,
                  query=query, statuses=Run.STATUS, page=Paginator(runs, 20).get_page(request.GET.get("page")))


@staff_member_required(login_url="admin:login")
@permission_required("panel.view_job", raise_exception=True)
def jobs(request):
    query = request.GET.get("q", "").strip()
    group = request.GET.get("group", "")
    state = request.GET.get("state", "active")
    records = Job.objects.filter(is_deleted=False, archived=state == "archived").select_related("connection", "group").order_by("name")
    if group == "__ungrouped__":
        records = records.filter(group__isnull=True)
    elif group.isdecimal():
        records = records.filter(group_id=int(group))
    if query:
        records = records.filter(Q(name__icontains=query) | Q(source_table__icontains=query) | Q(target_table__icontains=query) | Q(procedure_name__icontains=query))
    return screen(request, "jobs.html", section="jobs", title="Задания", query=query, group=group, state=state,
                  groups=JobGroup.objects.all(),
                  page=Paginator(records, 12).get_page(request.GET.get("page")))


@staff_member_required(login_url="admin:login")
@permission_required("panel.view_connection", raise_exception=True)
def connections(request):
    return screen(request, "connections.html", section="connections", title="Подключения",
                  connections=Connection.objects.order_by("name"))


class ConnectionForm(forms.ModelForm):
    class Meta:
        model = Connection
        fields = "__all__"


@staff_member_required(login_url="admin:login")
def edit(request, kind, pk=None):
    model, form_class, section = (Job, JobForm, "jobs") if kind == "job" else (Connection, ConnectionForm, "connections")
    permission = f"panel.{'change' if pk else 'add'}_{model._meta.model_name}"
    if not request.user.has_perm(permission):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    instance = get_object_or_404(model.objects.filter(is_deleted=False) if kind == "job" else model, pk=pk) if pk else None
    form = form_class(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        if kind == "job" and (not pk or {"cron", "timezone", "enabled"}.intersection(form.changed_data)):
            obj.next_run_at = None
        try:
            with transaction.atomic():
                obj.save()
                if kind == "job" and pk is None:
                    enqueue(obj, "check" if obj.job_type == "procedure" else "prepare")
        except ValidationError as exc:
            form.add_error(None, exc)
            return screen(request, "edit.html", section=section, title="Создание задания",
                          form=form, kind=kind)
        if kind == "job" and pk is None:
            messages.success(request, "Задание процедуры создано. Проверка подключения поставлена в очередь; сама процедура запускается вручную или по расписанию." if obj.job_type == "procedure" else "Задание создано. Проверка подключений, получение структуры и создание таблицы поставлены в очередь. Результат — в истории запусков.")
            return redirect("td_history")
        messages.success(request, "Изменения сохранены.")
        return redirect("td_" + section)
    return screen(request, "edit.html", section=section, title=("Настройка" if pk else "Создание") +
                  (" задания" if kind == "job" else " подключения"), form=form, kind=kind)


@staff_member_required(login_url="admin:login")
@permission_required("panel.change_job", raise_exception=True)
def action(request, pk, operation):
    job = get_object_or_404(Job.objects.filter(is_deleted=False).select_related("connection"), pk=pk)
    if job.job_type == "procedure" and operation == "run":
        if request.method == "GET":
            return redirect("td_action", pk=pk, operation="execute")
        from django.http import Http404
        raise Http404
    if (job.job_type == "procedure" and operation not in {"check", "execute"}) or (job.job_type != "procedure" and operation == "execute"):
        from django.http import Http404
        raise Http404
    if operation not in dict(Run.ACTIONS):
        from django.http import Http404
        raise Http404
    if request.method == "POST":
        try:
            _, created = enqueue(job, operation)
            messages.success(request, "Действие поставлено в очередь." if created else "Для этого задания уже есть активная операция.")
            return redirect("td_history")
        except ValidationError as exc:
            messages.error(request, "Проверьте настройки задания: " + "; ".join(exc.messages))
    return screen(request, "confirm.html", section="jobs", title=dict(Run.ACTIONS)[operation],
                  job=job, operation=operation)


@staff_member_required(login_url="admin:login")
@permission_required("panel.view_run", raise_exception=True)
def run_detail(request, pk):
    return screen(request, "run.html", section="history", title="Результат операции",
                  run=get_object_or_404(Run.objects.select_related("job"), pk=pk))
