from datetime import datetime, timedelta, timezone as dt_timezone
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command, CommandError
from django.test import Client, TestCase
from django.utils import timezone
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, select

from .models import Connection, Job, Run
from .services import enqueue, execute_next, schedule_due


class PanelTests(TestCase):
    def setUp(self):
        self.connection = Connection.objects.create(name="Test", target_host="pg.test", target_database="analytics")
        self.job = Job.objects.create(name="contractors", connection=self.connection,
                                      source_table="Справочники.Контрагенты", target_table="contractors")
        self.user = get_user_model().objects.create_superuser("operator", "operator@example.test", "test-password-only")

    def test_login_required_and_dashboard_is_rendered(self):
        self.assertEqual(self.client.get("/").status_code, 302)
        self.client.force_login(self.user)
        response = self.client.get("/")
        self.assertContains(response, "TransferData")
        self.assertContains(response, "SQL Server")

    def test_custom_login_authenticates_existing_user(self):
        self.assertContains(self.client.get("/login/"), "С возвращением")
        self.assertRedirects(self.client.post("/login/", {"username": "operator",
            "password": "test-password-only", "next": "/"}), "/")

    def test_nonstaff_cannot_access_panel(self):
        self.user.is_staff = False
        self.user.save()
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/panel/job/").status_code, 302)

    def test_job_forms_and_readonly_history_render(self):
        self.client.force_login(self.user)
        run, _ = enqueue(self.job, "check")
        for url in ["/connections/", f"/panel/connection/{self.connection.pk}/change/",
                    "/jobs/", f"/panel/job/{self.job.pk}/change/", "/runs/",
                    f"/panel/run/{run.pk}/change/"]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.get("/panel/run/add/").status_code, 403)

    def test_studio_forms_and_history_require_permissions(self):
        self.client.force_login(self.user)
        for url in ["/jobs/new/", f"/jobs/{self.job.pk}/edit/", "/connections/new/",
                    f"/connections/{self.connection.pk}/edit/"]:
            self.assertEqual(self.client.get(url).status_code, 200)
        self.user.is_superuser = False
        self.user.save()
        self.assertEqual(self.client.get("/runs/").status_code, 403)
        self.assertEqual(self.client.get(f"/jobs/{self.job.pk}/edit/").status_code, 403)

    def test_studio_confirmation_and_queue_only_on_post(self):
        self.client.force_login(self.user)
        url = f"/jobs/{self.job.pk}/run/"
        self.assertContains(self.client.get(url), "Подтвердить")
        self.assertFalse(Run.objects.exists())
        self.assertRedirects(self.client.post(url), "/runs/")
        self.assertEqual(Run.objects.get().status, "queued")

    def test_studio_csrf_and_job_edit_preserve_mapping(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(f"/jobs/{self.job.pk}/run/").status_code, 403)
        self.client.force_login(self.user)
        data = {"name": "renamed", "connection": self.connection.pk, "source_schema": "erp",
                "source_table": self.job.source_table, "target_schema": "public", "target_table": "contractors",
                "cron": "0 3 * * *", "timezone": "Europe/Moscow", "batch_size": 1000,
                "columns": json.dumps({"Имя": "name"}), "enabled": "on"}
        response = self.client.post(f"/jobs/{self.job.pk}/edit/", data)
        self.assertRedirects(response, "/jobs/")
        self.job.refresh_from_db()
        self.assertEqual(self.job.columns, {"Имя": "name"})
        self.assertEqual(self.job.cron, "0 3 * * *")
        self.assertIsNone(self.job.next_run_at)

    def test_action_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        response = client.post("/panel/job/", {"action": "check_connections", "_selected_action": self.job.pk})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Run.objects.exists())

    def test_load_requires_confirmation_and_only_enqueues(self):
        self.client.force_login(self.user)
        payload = {"action": "run_transfer", "_selected_action": self.job.pk}
        response = self.client.post("/panel/job/", payload)
        self.assertContains(response, "Подтвердить")
        self.assertFalse(Run.objects.exists())
        response = self.client.post("/panel/job/", {**payload, "confirm_etl": "yes"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Run.objects.get().status, "queued")

    def test_duplicate_queue_is_rejected_and_snapshot_is_immutable(self):
        run, created = enqueue(self.job, "run")
        self.assertTrue(created)
        same, created = enqueue(self.job, "check")
        self.assertFalse(created)
        self.assertEqual(same.pk, run.pk)
        self.job.source_table = "another"
        self.job.save()
        run.refresh_from_db()
        self.assertEqual(run.snapshot["jobs"][0]["source_table"], "Справочники.Контрагенты")

    def test_invalid_mapping_cron_and_long_names_rejected(self):
        for field, value in [("columns", {"a": "same", "b": "same"}),
                             ("columns", {"a": "Я" * 32}),
                             ("target_table", "Я" * 32), ("cron", "bad")]:
            with self.subTest(field=field, value=value):
                job = Job.objects.get(pk=self.job.pk)
                setattr(job, field, value)
                with self.assertRaises(ValidationError):
                    job.full_clean()

    def test_schedule_uses_moscow_and_persists_next_run(self):
        now = datetime(2026, 10, 21, 22, 0, tzinfo=dt_timezone.utc)  # 01:00 Moscow
        self.job.enabled = True
        self.job.save()
        schedule_due(now)
        self.job.refresh_from_db()
        self.assertEqual(self.job.next_run_at, now + timedelta(hours=1))
        self.assertFalse(Run.objects.exists())
        schedule_due(now + timedelta(hours=1, seconds=1))
        self.assertEqual(Run.objects.count(), 1)
        self.job.refresh_from_db()
        self.assertGreater(self.job.next_run_at, now + timedelta(days=1))
        schedule_due(now + timedelta(hours=1, seconds=2))
        self.assertEqual(Run.objects.count(), 1)

    def test_disabled_schedule_does_not_queue(self):
        self.job.next_run_at = timezone.now() - timedelta(days=1)
        self.job.save()
        schedule_due()
        self.assertFalse(Run.objects.exists())

    def test_restart_marks_uncertain_results_without_retry(self):
        run, _ = enqueue(self.job, "run")
        run.status = "running"
        run.save()
        with tempfile.TemporaryDirectory() as directory, self.settings(STATE_DIR=Path(directory)):
            call_command("etlworker", once=True)
        run.refresh_from_db()
        self.assertEqual(run.status, "interrupted")
        self.assertEqual(Run.objects.count(), 1)

    def test_driver_failure_is_sanitized(self):
        run, _ = enqueue(self.job, "run")
        with patch("transferdata.web.panel.services.engines", side_effect=RuntimeError("password=secret-data")):
            self.assertTrue(execute_next())
        run.refresh_from_db()
        self.assertEqual(run.status, "failed")
        self.assertNotIn("secret-data", run.message)
        self.assertIsNotNone(run.finished_at)

    def test_import_preserves_unicode_mapping_tls_and_disables_schedule(self):
        config = self.job.config()
        config["jobs"][0]["name"] = "imported"
        config["jobs"][0]["columns"] = {"ОченьДлинноеИмя": "short_name"}
        config["target"].update({"password": "must-not-be-stored", "sslmode": "require"})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8-sig")
            call_command("import_config", str(path), name="Imported")
            with self.assertRaises(CommandError):
                call_command("import_config", str(path), name="Imported")
        job = Job.objects.get(name="imported")
        self.assertFalse(job.enabled)
        self.assertEqual(job.columns, config["jobs"][0]["columns"])
        self.assertEqual(job.connection.sslmode, "require")
        self.assertNotIn("must-not-be-stored", json.dumps(job.config()))

    def test_worker_create_and_transfer_real_data(self):
        with tempfile.TemporaryDirectory() as directory:
            src = create_engine(f"sqlite:///{directory}/src.db")
            dst = create_engine(f"sqlite:///{directory}/dst.db")
            source = Table(self.job.source_table, MetaData(), Column("Код", Integer), Column("Имя", String(50)))
            source.create(src)
            with src.begin() as connection:
                connection.execute(source.insert(), [{"Код": 1, "Имя": "ООО Тест"}, {"Код": 2, "Имя": "ИП Пример"}])
            self.job.source_schema = ""
            self.job.target_schema = ""
            # Tests use SQLite (schema None), production forms require actual schemas.
            self.job.columns = {"Код": "code", "Имя": "name"}
            self.job.save()
            config = self.job.config()
            config["jobs"][0]["source_schema"] = None
            config["jobs"][0]["target_schema"] = None
            try:
                for action in ["check", "inspect", "prepare", "run"]:
                    run = Run.objects.create(job=self.job, action=action, snapshot=config)
                    with patch("transferdata.web.panel.services.engines", return_value=(src, dst)):
                        self.assertTrue(execute_next())
                    run.refresh_from_db()
                    self.assertEqual(run.status, "success", run.message)
                    if action == "inspect":
                        self.assertEqual(len(run.details["columns"]), 2)
                self.assertEqual(run.rows, 2)
                target = Table("contractors", MetaData(), autoload_with=dst)
                with dst.connect() as connection:
                    self.assertEqual(connection.execute(select(target).order_by(target.c.code)).all(),
                                     [(1, "ООО Тест"), (2, "ИП Пример")])
            finally:
                src.dispose()
                dst.dispose()
