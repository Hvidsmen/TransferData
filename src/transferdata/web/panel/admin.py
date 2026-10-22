from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.contrib.auth.admin import GroupAdmin, UserAdmin
from django.contrib.auth.models import Group, User
from django.template.response import TemplateResponse
from django.utils.html import format_html

from .models import Connection, Job, Run
from .forms import JobForm
from .services import enqueue


class PanelSite(admin.AdminSite):
    site_header = "ETL Dev · TransferData"
    site_title = "ETL Dev"
    index_title = "Обзор"
    index_template = "panel/index.html"

    def index(self, request, extra_context=None):
        stats = None
        if request.user.has_perm("panel.view_run"):
            stats = {"queued": Run.objects.filter(status="queued").count(),
                     "running": Run.objects.filter(status="running").count(),
                     "failed": Run.objects.filter(status="failed").count(),
                     "success": Run.objects.filter(status="success").count(),
                     "recent": Run.objects.select_related("job")[:8]}
        return super().index(request, {**(extra_context or {}), "etl_stats": stats})


site = PanelSite(name="admin")
site.register(User, UserAdmin)
site.register(Group, GroupAdmin)


@admin.register(Connection, site=site)
class ConnectionAdmin(admin.ModelAdmin):
    change_list_template = "panel/change_list.html"
    list_display = ["name", "source_host", "source_database", "target_host", "target_database"]
    search_fields = ["name", "source_host", "target_host"]
    fieldsets = [
        (None, {"fields": ["name"]}),
        ("SQL Server · Windows-вход от имени worker", {"fields": [
            "source_host", "source_database", "source_driver", "source_trust"]}),
        ("PostgreSQL", {"fields": ["target_host", "target_port", "target_database", "sslmode",
                                      "username_env", "password_env"]}),
    ]


@admin.register(Job, site=site)
class JobAdmin(admin.ModelAdmin):
    change_list_template = "panel/change_list.html"
    form = JobForm
    list_display = ["name", "source_table", "target_table", "enabled", "cron", "timezone", "next_run_at"]
    list_filter = ["enabled", "connection"]
    search_fields = ["name", "source_table", "target_table"]
    readonly_fields = ["next_run_at"]
    actions = ["check_connections", "inspect_source", "prepare_table", "run_transfer"]
    fieldsets = [
        (None, {"fields": ["name", "connection"]}),
        ("Источник и назначение", {"fields": ["source_schema", "source_table", "target_schema", "target_table"]}),
        ("Расписание", {"fields": ["enabled", "cron", "timezone", "next_run_at"]}),
        ("Загрузка", {"fields": ["batch_size", "allow_empty", "columns"],
                      "description": "Полная замена в транзакции. Создание таблицы выполняется отдельным действием."}),
    ]

    def save_model(self, request, obj, form, change):
        if not change or {"cron", "timezone", "enabled"}.intersection(form.changed_data):
            obj.next_run_at = None
        super().save_model(request, obj, form, change)

    def queue_action(self, request, queryset, action, confirm=False):
        if confirm and request.POST.get("confirm_etl") != "yes":
            return TemplateResponse(request, "panel/confirm.html", {
                **self.admin_site.each_context(request), "title": dict(Run.ACTIONS)[action],
                "jobs": queryset, "action_name": request.POST.get("action"),
                "checkbox_name": ACTION_CHECKBOX_NAME, "opts": self.model._meta,
                "action_label": dict(Run.ACTIONS)[action],
            })
        count = 0
        for job in queryset.select_related("connection"):
            _, created = enqueue(job, action)
            count += created
        self.message_user(request, f"Добавлено в очередь: {count}. Повторные активные задания не добавлены. "
                                  "Результаты появятся в истории после обработки worker.", messages.SUCCESS)

    @admin.action(description="Проверить подключения", permissions=["change"])
    def check_connections(self, request, queryset):
        return self.queue_action(request, queryset, "check")

    @admin.action(description="Получить структуру источника", permissions=["change"])
    def inspect_source(self, request, queryset):
        return self.queue_action(request, queryset, "inspect")

    @admin.action(description="Создать целевую таблицу…", permissions=["change"])
    def prepare_table(self, request, queryset):
        return self.queue_action(request, queryset, "prepare", confirm=True)

    @admin.action(description="Запустить полную загрузку…", permissions=["change"])
    def run_transfer(self, request, queryset):
        return self.queue_action(request, queryset, "run", confirm=True)


@admin.register(Run, site=site)
class RunAdmin(admin.ModelAdmin):
    change_list_template = "panel/change_list.html"
    list_display = ["run_number", "job", "action", "status_badge", "created_at", "rows", "seconds"]
    list_filter = ["status", "action", "job"]
    list_select_related = ["job"]
    fields = ["job", "action", "status", "created_at", "started_at", "finished_at", "rows", "seconds", "message", "details"]
    readonly_fields = fields

    @admin.display(description="Запуск", ordering="id")
    def run_number(self, obj):
        return f"#{obj.pk:04d}"

    @admin.display(description="Состояние", ordering="status")
    def status_badge(self, obj):
        return format_html('<span class="td-badge td-badge--{}"><span class="td-dot"></span>{}</span>',
                           obj.status, obj.get_status_display())

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
