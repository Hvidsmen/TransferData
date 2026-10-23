from datetime import datetime, timedelta, timezone as dt_timezone
import json
from pathlib import Path
import tempfile
from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command, CommandError
from django.test import Client, TestCase
from django.utils import timezone
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, select, text

from .models import Connection, Job, JobGroup, Run
from .services import enqueue, execute_next, schedule_due


class PanelTests(TestCase):
    def test_corporate_domain_and_https_origin_accept_login(self):
        host = 'etl-dev-portal.daichi.ru'
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.get('/login/', HTTP_HOST=host).status_code, 200)
        token = client.cookies['csrftoken'].value
        response = client.post('/login/', {'username': 'operator', 'password': 'test-password-only',
            'csrfmiddlewaretoken': token}, HTTP_HOST=host, HTTP_ORIGIN='https://' + host)
        self.assertEqual(response.status_code, 302)
        response = client.post('/login/', {'csrfmiddlewaretoken': client.cookies['csrftoken'].value},
            HTTP_HOST=host, HTTP_ORIGIN='https://untrusted.example')
        self.assertEqual(response.status_code, 403)

    def test_display_keeps_whole_groups_together_and_counts_all_jobs(self):
        self.client.force_login(self.user)
        group = JobGroup.objects.create(name='Облака')
        self.job.group = group
        self.job.save()
        for index in range(13):
            Job.objects.create(name=f'Grouped {index}', group=group, connection=self.connection,
                               source_table='Example', target_table=f'example_{index}')
        response = self.client.get('/jobs/')
        section = response.context['job_sections'][0]
        self.assertEqual(section['name'], 'Облака')
        self.assertEqual(section['count'], 14)
        self.assertEqual(len(section['jobs']), 14)
        self.assertContains(response, '<details class="job-group"')
        for index in range(7):
            g = JobGroup.objects.create(name=f'Группа {index}')
            Job.objects.create(name=f'Another {index}', group=g, connection=self.connection,
                               source_table='Example', target_table=f'another_{index}')
        first = self.client.get('/jobs/').context['job_sections']
        second = self.client.get('/jobs/', {'page': 2}).context['job_sections']
        self.assertEqual(len(first), 6)
        self.assertEqual(len(second), 2)
        self.assertFalse({s['id'] for s in first} & {s['id'] for s in second})
        filtered = self.client.get('/jobs/', {'q': 'Grouped 1'}).context['job_sections']
        self.assertEqual(filtered[0]['count'], 4)

    def test_group_catalogue_rename_and_delete_keep_jobs(self):
        self.client.force_login(self.user)
        self.assertRedirects(self.client.post('/groups/', {'name': 'Справочники'}), '/groups/')
        group = JobGroup.objects.get(name='Справочники')
        self.job.group = group
        self.job.save()
        self.client.post('/groups/', {'pk': group.pk, 'name': 'Новая группа'})
        self.job.refresh_from_db()
        self.assertEqual(self.job.group.name, 'Новая группа')
        response = self.client.post('/groups/', {'pk': group.pk, 'operation': 'delete'})
        self.assertContains(response, 'сохранятся')
        self.assertTrue(JobGroup.objects.filter(pk=group.pk).exists())
        self.client.post('/groups/', {'pk': group.pk, 'operation': 'delete', 'confirmed': 'yes'})
        self.job.refresh_from_db()
        self.assertIsNone(self.job.group)

    def test_bulk_group_archive_restore_and_delete_preserve_history(self):
        self.client.force_login(self.user)
        group = JobGroup.objects.create(name='Справочники')
        payload = {'selected': [self.job.pk], 'operation': 'group', 'group': group.pk}
        self.assertContains(self.client.post('/jobs/bulk/', payload), 'Подтвердить')
        self.job.refresh_from_db()
        self.assertIsNone(self.job.group)
        self.client.post('/jobs/bulk/', {**payload, 'confirmed': 'yes'})
        self.job.refresh_from_db()
        self.assertEqual(self.job.group, group)
        run, _ = enqueue(self.job, 'check')
        run.status = 'success'
        run.save()
        self.job.enabled = True
        self.job.save()
        payload = {'selected': [self.job.pk], 'operation': 'archive', 'confirmed': 'yes'}
        self.client.post('/jobs/bulk/', payload)
        self.job.refresh_from_db()
        self.assertTrue(self.job.archived)
        self.assertFalse(self.job.enabled)
        self.assertNotContains(self.client.get('/jobs/'), 'aria-label="Выбрать contractors"')
        self.assertContains(self.client.get('/jobs/', {'state': 'archived'}), 'aria-label="Выбрать contractors"')
        with self.assertRaises(ValidationError):
            enqueue(self.job, 'run')
        self.client.post('/jobs/bulk/', {**payload, 'operation': 'restore'})
        self.job.refresh_from_db()
        self.assertFalse(self.job.archived)
        self.assertFalse(self.job.enabled)
        self.client.post('/jobs/bulk/', {**payload, 'operation': 'delete'})
        self.job.refresh_from_db()
        self.assertTrue(self.job.is_deleted)
        self.assertTrue(Run.objects.filter(pk=run.pk).exists())
        self.assertEqual(self.client.get(f'/jobs/{self.job.pk}/edit/').status_code, 404)

    def test_bulk_restarts_skip_active_and_archive_is_atomic(self):
        self.client.force_login(self.user)
        other = Job.objects.create(name='Other', connection=self.connection, source_table='Other', target_table='other')
        run, _ = enqueue(self.job, 'check')
        payload = {'selected': [self.job.pk, other.pk], 'operation': 'archive', 'confirmed': 'yes'}
        self.client.post('/jobs/bulk/', payload)
        self.assertFalse(Job.objects.filter(archived=True).exists())
        self.client.post('/jobs/bulk/', {**payload, 'operation': 'restart'})
        self.assertEqual(Run.objects.filter(job=self.job).count(), 1)
        self.assertEqual(Run.objects.get(job=other).action, 'run')
        run.refresh_from_db()
        self.assertEqual(run.status, 'queued')

    def test_bulk_requires_permissions_csrf_and_valid_selection(self):
        self.client.force_login(self.user)
        payload = {'selected': [999999], 'operation': 'delete', 'confirmed': 'yes'}
        self.assertEqual(self.client.post('/jobs/bulk/', payload).status_code, 400)
        self.assertEqual(self.client.get('/jobs/bulk/').status_code, 405)
        secured = Client(enforce_csrf_checks=True)
        secured.force_login(self.user)
        self.assertEqual(secured.post('/jobs/bulk/', payload).status_code, 403)
        self.user.is_superuser = False
        self.user.save()
        self.assertEqual(self.client.post('/jobs/bulk/', payload).status_code, 403)
        self.assertEqual(self.client.post('/groups/', {'name': 'Forbidden'}).status_code, 403)

    def test_failed_connection_stops_preparation_before_structure_and_ddl(self):
        run, _ = enqueue(self.job, "prepare")
        source = MagicMock()
        source.connect.side_effect = RuntimeError("must-not-be-stored")
        with patch("transferdata.web.panel.services.engines", return_value=(source, MagicMock())), \
             patch("transferdata.web.panel.services.prepare_target") as create, \
             patch("transferdata.web.panel.services.inspect") as structure:
            execute_next()
            create.assert_not_called()
            structure.assert_not_called()
        run.refresh_from_db()
        self.job.refresh_from_db()
        self.assertEqual(run.status, "failed")
        self.assertEqual(run.details["steps"], [{"label": "1. Проверка подключений", "status": "failed"}])
        self.assertEqual(self.job.columns, {})
        self.assertNotIn("must-not-be-stored", run.message)

    def test_auto_preparation_saves_columns_before_creating_table(self):
        with tempfile.TemporaryDirectory() as directory:
            src = create_engine(f"sqlite:///{directory}/src.db")
            dst = create_engine(f"sqlite:///{directory}/dst.db")
            long_name = "ОченьДлинноеИмяСтолбца" * 4
            table = Table("source", MetaData(), Column("Код", Integer), Column(long_name, String(20)))
            table.create(src)
            self.job.source_schema = "main"
            self.job.target_schema = "main"
            self.job.source_table = "source"
            self.job.save()
            run, _ = enqueue(self.job, "prepare")
            with patch("transferdata.web.panel.services.engines", return_value=(src, dst)):
                execute_next()
            run.refresh_from_db()
            self.job.refresh_from_db()
            self.assertEqual(run.status, "success", run.message)
            self.assertEqual(self.job.columns["Код"], "Код")
            self.assertLessEqual(len(self.job.columns[long_name].encode("utf-8")), 63)
            self.assertEqual(run.snapshot["jobs"][0]["columns"], self.job.columns)
            self.assertEqual(run.details["mapping"], self.job.columns)
            self.assertEqual(len(run.details["steps"]), 4)
            self.assertTrue(all(s["status"] == "success" for s in run.details["steps"]))
            replica = Table("contractors", MetaData(), autoload_with=dst)
            self.assertEqual(set(replica.c.keys()), set(self.job.columns.values()))
            with dst.connect() as connection:
                self.assertEqual(connection.scalar(text("SELECT count(*) FROM contractors")), 0)
            src.dispose()
            dst.dispose()

    def test_create_procedure_checks_only_source_and_groups_filter(self):
        self.client.force_login(self.user)
        group = JobGroup.objects.create(name="Регламентные")
        self.connection.target_host = ""
        self.connection.target_database = ""
        self.connection.save()
        data = {"name": "Refresh models", "job_type": "procedure", "group": group.pk,
                "connection": self.connection.pk, "procedure_schema": "dbo", "procedure_name": "RefreshModels",
                "procedure_parameters": '{"FullLoad": true}', "procedure_timeout": 120,
                "cron": "0 3 * * *", "timezone": "Europe/Moscow"}
        response = self.client.post("/jobs/new/", data)
        self.assertRedirects(response, "/runs/")
        job = Job.objects.get(name="Refresh models")
        self.assertEqual(job.job_type, "procedure")
        self.assertEqual(Run.objects.get(job=job).action, "check")
        with patch("transferdata.web.panel.services.source_engine") as source, \
             patch("transferdata.web.panel.services.engines") as target:
            source.return_value.connect.return_value.__enter__.return_value.scalar.return_value = 1
            execute_next()
            target.assert_not_called()
        self.assertEqual(Run.objects.get(job=job).status, "success")
        with self.assertRaises(ValidationError):
            enqueue(job, "prepare")
        run, _ = enqueue(job, "run")
        self.assertEqual(run.action, "execute")
        with patch("transferdata.web.panel.services.source_engine") as source, \
             patch("transferdata.web.panel.services.engines") as target, \
             patch("transferdata.web.panel.services.execute_procedure", return_value=0) as execute:
            self.assertTrue(execute_next())
            target.assert_not_called()
            execute.assert_called_once_with(source.return_value, run.snapshot["jobs"][0])
        run.refresh_from_db()
        self.assertEqual(run.status, "success")
        self.assertEqual(run.details, {"return_code": 0})
        self.assertContains(self.client.get("/jobs/", {"group": group.pk}), "dbo.RefreshModels")
        self.assertNotContains(self.client.get("/jobs/", {"group": "__ungrouped__"}), "dbo.RefreshModels")
        self.assertEqual(self.client.get(f"/jobs/{job.pk}/prepare/").status_code, 404)
        self.assertContains(self.client.get(f"/runs/{run.pk}/"), "dbo.RefreshModels")
        self.assertRedirects(self.client.get(f"/jobs/{job.pk}/run/"), f"/jobs/{job.pk}/execute/")
        self.assertContains(self.client.get(f"/jobs/{job.pk}/execute/"), "Выполнить процедуру")
        self.assertEqual(self.client.post(f"/jobs/{job.pk}/run/").status_code, 404)

    def test_procedure_schedule_queues_execution_and_preserves_snapshot(self):
        job = Job.objects.create(name="proc", job_type="procedure", connection=self.connection,
                                 procedure_name="Refresh", procedure_parameters={"Flag": True},
                                 enabled=True, next_run_at=timezone.now() - timedelta(minutes=1))
        schedule_due()
        run = Run.objects.get(job=job)
        self.assertEqual(run.action, "execute")
        job.procedure_name = "Changed"
        job.save()
        self.assertEqual(run.snapshot["jobs"][0]["procedure_name"], "Refresh")
        schedule_due()
        self.assertEqual(Run.objects.filter(job=job).count(), 1)

    def test_procedure_requires_name_and_input_parameters(self):
        job = Job(name="proc", job_type="procedure", connection=self.connection)
        with self.assertRaises(ValidationError):
            job.full_clean()
        job.procedure_name = "Refresh"
        job.procedure_parameters = {"x": {"unsupported": 1}}
        with self.assertRaises(ValidationError):
            job.full_clean()
        with self.assertRaises(ValidationError):
            enqueue(self.job, "execute")

    def test_new_job_queues_preparation_but_edit_does_not(self):
        self.client.force_login(self.user)
        data = {"name": "new route", "connection": self.connection.pk,
                "source_schema": "erp", "source_table": "Example", "target_schema": "public",
                "target_table": "example", "cron": "0 3 * * *", "timezone": "Europe/Moscow",
                "batch_size": 1000, "columns": "{}"}
        self.assertRedirects(self.client.post("/jobs/new/", data), "/runs/")
        job = Job.objects.get(name="new route")
        run = Run.objects.get(job=job)
        self.assertEqual((run.action, run.status), ("prepare", "queued"))
        self.assertEqual(run.snapshot["jobs"][0]["source_table"], "Example")
        self.assertRedirects(self.client.post(f"/jobs/{job.pk}/edit/", data), "/jobs/")
        self.assertEqual(Run.objects.filter(job=job).count(), 1)

    def test_failed_auto_preparation_does_not_save_new_job(self):
        self.client.force_login(self.user)
        data = {"name": "new route", "connection": self.connection.pk,
                "source_schema": "erp", "source_table": "Example", "target_schema": "public",
                "target_table": "example", "cron": "0 3 * * *", "timezone": "Europe/Moscow",
                "batch_size": 1000, "columns": "{}"}
        with patch("transferdata.web.panel.studio.enqueue", side_effect=ValidationError("Maintenance")):
            self.assertContains(self.client.post("/jobs/new/", data), "Maintenance")
        self.assertFalse(Job.objects.filter(name="new route").exists())

    def setUp(self):
        self.connection = Connection.objects.create(name="Test", target_host="pg.test", target_database="analytics")
        self.job = Job.objects.create(name="contractors", connection=self.connection,
                                      source_table="Справочники.Контрагенты", target_table="contractors")
        self.user = get_user_model().objects.create_superuser("operator", "operator@example.test", "test-password-only")

    def test_login_required_and_dashboard_is_rendered(self):
        self.assertEqual(self.client.get("/").status_code, 302)
        self.client.force_login(self.user)
        response = self.client.get("/")
        self.assertContains(response, "SQL Перенос данных")
        self.assertContains(response, "SQL Server")

    def test_custom_login_authenticates_existing_user(self):
        self.assertContains(self.client.get("/login/"), "С возвращением")
        self.assertRedirects(self.client.post("/login/", {"username": "operator",
            "password": "test-password-only", "next": "/"}), "/")

    def test_portal_separates_subsystems_and_keeps_transferdata(self):
        self.client.force_login(self.user)
        response = self.client.get("/")
        self.assertContains(response, "ETL Dev")
        self.assertContains(response, "1С Перенос данных")
        self.assertContains(response, 'href="/erp/"')
        self.assertContains(response, 'href="/transferdata/"')
        response = self.client.get("/transferdata/")
        self.assertContains(response, "Данные в движении")
        self.assertEqual(self.client.get("/jobs/").status_code, 200)

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

    def test_maintenance_pauses_worker_and_rejects_new_jobs(self):
        from transferdata.web.maintenance import worker_paused
        run, _ = enqueue(self.job, "run")
        with tempfile.TemporaryDirectory() as directory, self.settings(STATE_DIR=Path(directory)):
            (Path(directory) / "maintenance.flag").touch()
            call_command("etlworker", once=True)
            self.assertTrue((Path(directory) / "paused-transferdata").exists())
            run.refresh_from_db()
            self.assertEqual(run.status, "queued")
            with self.assertRaises(ValidationError):
                enqueue(self.job, "check")
            (Path(directory) / "maintenance.flag").unlink()
            self.assertFalse(worker_paused("transferdata"))
            self.assertFalse((Path(directory) / "paused-transferdata").exists())

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
                    if action in {"inspect", "prepare"}:
                        self.assertEqual(len(run.details["columns"]), 2)
                    if action == "prepare":
                        with dst.connect() as connection:
                            self.assertEqual(connection.scalar(text('SELECT count(*) FROM contractors')), 0)
                self.assertEqual(run.rows, 2)
                target = Table("contractors", MetaData(), autoload_with=dst)
                with dst.connect() as connection:
                    self.assertEqual(connection.execute(select(target).order_by(target.c.code)).all(),
                                     [(1, "ООО Тест"), (2, "ИП Пример")])
            finally:
                src.dispose()
                dst.dispose()

    def test_overview_monitor_has_one_execution_per_job_and_ignores_checks(self):
        self.client.force_login(self.user)
        Run.objects.create(job=self.job, action='run', status='failed', snapshot={}, rows=12)
        latest = Run.objects.create(job=self.job, action='run', status='success', snapshot={}, rows=99, seconds=2)
        Run.objects.create(job=self.job, action='check', status='failed', snapshot={})
        procedure = Job.objects.create(name='Процедура', connection=self.connection, job_type='procedure', procedure_name='sp_ETL')
        Run.objects.create(job=procedure, action='execute', status='running', snapshot={})
        never = Job.objects.create(name='Без запусков', connection=self.connection, source_table='Example', target_table='example')
        archived = Job.objects.create(name='Архив', connection=self.connection, archived=True)
        Job.objects.create(name='Удалено', connection=self.connection, is_deleted=True)
        response = self.client.get('/transferdata/')
        items = {item['name']: item for item in response.context['monitor_jobs']}
        self.assertEqual(len(items), 4)
        self.assertEqual(items[self.job.name]['status'], 'success')
        self.assertEqual(items[self.job.name]['rows'], 99)
        self.assertEqual(items[self.job.name]['run_url'], f'/runs/{latest.pk}/')
        self.assertEqual(items[procedure.name]['status'], 'running')
        self.assertEqual(items[never.name]['status'], 'never')
        self.assertTrue(items[archived.name]['archived'])
        self.assertContains(response, 'id="live-job-monitor"')
        self.assertContains(response, 'Поиск заданий в обзоре')

    def test_monitor_does_not_disclose_runs_without_permission(self):
        from django.contrib.auth.models import Permission
        from .monitoring import transferdata_jobs
        viewer = get_user_model().objects.create_user('monitor-viewer', is_staff=True)
        self.assertEqual(transferdata_jobs(viewer), [])
        viewer.user_permissions.add(Permission.objects.get(codename='view_job', content_type__app_label='panel'))
        viewer = get_user_model().objects.get(pk=viewer.pk)
        Run.objects.create(job=self.job, action='run', status='failed', snapshot={}, rows=123)
        items = transferdata_jobs(viewer)
        self.assertEqual(items[0]['status'], 'restricted')
        self.assertIsNone(items[0]['rows'])
        self.assertEqual(items[0]['run_url'], '')
        self.assertEqual(items[0]['url'], '')
