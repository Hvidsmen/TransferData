import json
from pathlib import Path

from django.core import serializers
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from transferdata.erp.control.models import DatabaseConnection, ETLJob, ETLExecution, ETLExecutionLog, ETLAuditLog


class Command(BaseCommand):
    help = "Import ERP_to_DWH dumpdata with natural user keys into empty subsystem tables."

    def add_arguments(self, parser):
        parser.add_argument("path")

    def handle(self, *args, **options):
        models = [DatabaseConnection, ETLJob, ETLExecution, ETLExecutionLog, ETLAuditLog]
        allowed = {model._meta.label_lower for model in models}
        payload = json.loads(Path(options["path"]).read_text(encoding="utf-8-sig"))
        if not isinstance(payload, list):
            raise CommandError("Expected a dumpdata JSON list.")
        for item in payload:
            if item.get("model") not in allowed:
                raise CommandError("Only ERP control models may be imported; auth and worker leases are excluded.")
            fields = item["fields"]
            for field in ["created_by", "triggered_by", "actor"]:
                if fields.get(field) is not None and not (
                    isinstance(fields[field], list) and len(fields[field]) == 1 and isinstance(fields[field][0], str)
                ):
                    raise CommandError("Export with --natural-foreign so user IDs cannot be assigned to the wrong portal user.")
            if item["model"] == "control.etljob":
                fields["schedule_enabled"] = False
            if item["model"] == "control.etlexecution" and fields["status"] in {"PENDING", "RUNNING"}:
                fields["status"] = "FAILED" if fields["status"] == "RUNNING" else "CANCELLED"
                fields["stage"] = "Imported without automatic retry"
                fields["error_message"] = "Перенесён незавершённый запуск. Проверьте назначение перед повтором."
        try:
            with transaction.atomic():
                if any(model.objects.exists() for model in models):
                    raise CommandError("ERP subsystem tables must be empty; import never overwrites existing data.")
                for obj in serializers.deserialize("json", json.dumps(payload)):
                    obj.save()
        except CommandError:
            raise
        except Exception as exc:
            raise CommandError(f"Import rolled back ({type(exc).__name__}). Check export order and matching portal usernames.") from None
        self.stdout.write(self.style.SUCCESS(f"Imported {len(payload)} records. Schedules are disabled; pending/running work is not retried."))
