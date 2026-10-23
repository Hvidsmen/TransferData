"""Shared group catalogue and explicit bulk operations for both subsystems."""
from django import forms
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from transferdata.web.maintenance import is_paused
from .models import Job, JobGroup, Run
from .studio import screen


class GroupForm(forms.ModelForm):
    class Meta:
        model = JobGroup
        fields = ["name"]


@staff_member_required(login_url="admin:login")
def groups(request):
    if not request.user.has_perm("panel.view_jobgroup"):
        raise PermissionDenied
    form = GroupForm()
    if request.method == "POST":
        operation = request.POST.get("operation", "save")
        permission = "delete" if operation == "delete" else ("change" if request.POST.get("pk") else "add")
        if not request.user.has_perm(f"panel.{permission}_jobgroup"):
            raise PermissionDenied
        if request.POST.get("pk") and not request.POST["pk"].isdecimal():
            return HttpResponseBadRequest("Invalid group")
        group = get_object_or_404(JobGroup, pk=request.POST["pk"]) if request.POST.get("pk") else None
        if is_paused():
            messages.error(request, "Портал обновляется. Повторите позже.")
            return redirect("etl_groups")
        if operation == "delete":
            if group is None:
                return HttpResponseBadRequest("Group required")
            if request.POST.get("confirmed") != "yes":
                return screen(request, "group_delete.html", title="Удаление группы", group=group, subsystem=None)
            group.delete()
            messages.success(request, "Группа удалена. Задания сохранены без группы.")
            return redirect("etl_groups")
        form = GroupForm(request.POST, instance=group)
        if form.is_valid():
            form.save()
            return redirect("etl_groups")
    return screen(request, "groups.html", title="Справочник групп", section="groups", subsystem=None,
                  groups=JobGroup.objects.all(), form=form)


LABELS = {"group": "Назначить группу", "restart": "Запустить повторно",
          "archive": "Отправить в архив", "restore": "Вернуть из архива", "delete": "Удалить задания"}


@staff_member_required(login_url="admin:login")
@require_POST
def bulk(request, subsystem="transferdata"):
    erp = subsystem == "erp"
    python = subsystem == "python"
    if python:
        from transferdata.python_jobs.models import Job as PythonJob, Run as PythonRun
        from transferdata.python_jobs.services import enqueue
        model, execution = PythonJob, PythonRun
        listing = "python_jobs:jobs"
    elif erp:
        from transferdata.erp.control.models import ETLJob, ETLExecution
        from transferdata.erp.control.services import enqueue
        model, execution = ETLJob, ETLExecution
        listing = "erp:jobs"
    else:
        from .services import enqueue
        model, execution = Job, Run
        listing = "td_jobs"
    operation = request.POST.get("operation")
    if operation not in LABELS:
        return HttpResponseBadRequest("Unknown operation")
    permission = "delete" if operation == "delete" else "change"
    if (erp and not request.user.is_superuser) or (not erp and not request.user.has_perm(f"{'python_jobs' if python else 'panel'}.{permission}_job")):
        raise PermissionDenied
    try:
        ids = list({int(value) for value in request.POST.getlist("selected")})
    except (ValueError, TypeError):
        return HttpResponseBadRequest("Invalid selection")
    if not ids or len(ids) > 500:
        messages.error(request, "Выберите от 1 до 500 заданий.")
        return redirect(listing)
    records = model.objects.filter(pk__in=ids, is_deleted=False).order_by("pk")
    if records.count() != len(ids):
        return HttpResponseBadRequest("Selection changed; reload the list")
    group = None
    if operation == "group" and request.POST.get("group"):
        if not request.POST["group"].isdecimal():
            return HttpResponseBadRequest("Invalid group")
        group = get_object_or_404(JobGroup, pk=request.POST["group"])
    if request.POST.get("confirmed") != "yes":
        return screen(request, "bulk_confirm.html", title=LABELS[operation], records=records,
                      operation=operation, group=group, listing=listing, subsystem=subsystem)
    try:
        if is_paused():
            raise ValidationError("Портал обновляется. Повторите позже.")
        count = skipped = 0
        with transaction.atomic():
            selected = list(records.select_for_update())
            if len(selected) != len(ids):
                raise ValidationError("Список изменился. Обновите страницу.")
            active_ids = set(execution.objects.filter(job_id__in=ids, status__in=
                ["PENDING", "RUNNING"] if erp else ["queued", "running"]).values_list("job_id", flat=True))
            if active_ids and operation in {"archive", "delete"}:
                raise ValidationError("Есть задания в очереди или выполняющиеся. Дождитесь завершения; изменения не применены.")
            for job in selected:
                if operation == "restart":
                    if job.pk in active_ids:
                        skipped += 1
                        continue
                    if erp:
                        enqueue(job, request.user)
                    else:
                        _, created = enqueue(job, "run")
                        if not created:
                            skipped += 1
                            continue
                elif operation == "group":
                    job.group = group
                    job.save(update_fields=["group"])
                else:
                    job.archived = operation != "restore"
                    if erp:
                        job.schedule_enabled = False
                        fields = ["archived", "schedule_enabled"]
                    else:
                        job.enabled = False
                        job.next_run_at = None
                        fields = ["archived", "enabled", "next_run_at"]
                    if operation == "delete":
                        job.is_deleted = True
                        fields.append("is_deleted")
                    job.save(update_fields=fields)
                if erp:
                    from transferdata.erp.control.models import ETLAuditLog
                    ETLAuditLog.objects.create(actor=request.user, object_type="ETLJob", object_id=job.pk, action="bulk_" + operation)
                count += 1
        messages.success(request, f"Обработано заданий: {count}. Уже активных пропущено: {skipped}." +
                         (" История запусков сохранена." if operation == "delete" else ""))
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect(listing)
