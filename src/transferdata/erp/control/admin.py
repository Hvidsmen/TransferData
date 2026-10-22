from django.contrib import admin
from transferdata.web.panel.admin import site as portal_site
from .models import DatabaseConnection, ETLJob, ETLExecution, ETLExecutionLog, ETLAuditLog, WorkerLease

class AuditAdmin(admin.ModelAdmin):
    def has_add_permission(self, request): return request.user.is_superuser
    def has_change_permission(self, request, obj=None): return request.user.is_superuser
    def save_model(self, request, obj, form, change):
        if isinstance(obj, ETLJob) and not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
        ETLAuditLog.objects.create(actor=request.user, object_type=type(obj).__name__, object_id=obj.pk, action='update' if change else 'create')
    def has_delete_permission(self, request, obj=None):
        return False

class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False

portal_site.register(DatabaseConnection, AuditAdmin)
portal_site.register(ETLJob, AuditAdmin)
for model in [ETLExecution, ETLExecutionLog, ETLAuditLog, WorkerLease]:
    portal_site.register(model, ReadOnlyAdmin)
