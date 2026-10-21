import json
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from transferdata.cli import validate
from transferdata.web.panel.models import Connection, Job


class Command(BaseCommand):
    help = "Import existing non-secret config.json and all mappings. Existing records are preserved."

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--name", default="Основное подключение")

    def handle(self, *args, **options):
        config = json.loads(Path(options["path"]).read_text(encoding="utf-8-sig"))
        validate(config)
        source, target = config["source"], config["target"]
        if source.get("auth") != "windows" or target.get("dialect") != "postgresql":
            raise CommandError("Panel import supports Windows SQL Server to PostgreSQL.")
        # Only known non-secret fields are stored; never copy arbitrary JSON.
        with transaction.atomic():
            if Connection.objects.filter(name=options["name"]).exists():
                raise CommandError("Connection already exists; import does not overwrite existing settings.")
            connection = Connection(
                name=options["name"], source_host=source["host"], source_database=source["database"],
                source_driver=source.get("driver", "ODBC Driver 17 for SQL Server"),
                source_trust=source.get("trust_server_certificate", False),
                target_host=target["host"], target_port=target.get("port", 5432), target_database=target["database"],
                username_env=target.get("username_env", "TARGET_USER"),
                password_env=target.get("password_env", "TARGET_PASSWORD"), sslmode=target.get("sslmode", "verify-full"))
            connection.full_clean()
            connection.save()
            for item in config["jobs"]:
                job = Job(connection=connection, name=item["name"], source_schema=item.get("source_schema", "dbo"),
                          source_table=item["source_table"], target_schema=item.get("target_schema", "public"),
                          target_table=item["target_table"], cron=item["cron"], timezone=config.get("timezone", "Europe/Moscow"),
                          batch_size=item.get("batch_size", 1000), allow_empty=item.get("allow_empty", False),
                          columns=item.get("columns", {}), enabled=False)
                job.full_clean()
                job.save()
        self.stdout.write(self.style.SUCCESS("Imported. Schedules are disabled until enabled in the panel."))
