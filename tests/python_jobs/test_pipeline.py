import copy
from unittest.mock import patch
import pyodbc
import pytest
from django.core.exceptions import ValidationError
from django.db import OperationalError
from django.urls import reverse
from django.test import Client
from transferdata.erp.control.models import DatabaseConnection
from transferdata.python_jobs.models import Job, Run, StepRun
from transferdata.python_jobs.services import enqueue
from transferdata.python_jobs.pipeline import execute_pipeline, request_stop, recover_steps
from transferdata.python_jobs.steps import validate_steps, procedure_sql

pytestmark = pytest.mark.django_db


@pytest.fixture
def pipeline(settings, tmp_path):
    settings.STATE_DIR = tmp_path
    connection = DatabaseConnection.objects.create(name='SQL steps', server='MSK-DB2', database='DataWH', authentication_type='WINDOWS')
    steps = [{'id': str(i), 'name': f'Step {i}', 'database': 'DataWH', 'schema': 'dbo', 'procedure': f'proc_{i}',
              'enabled': True, 'depends_on': [] if i == 1 else [str(i-1)], 'parameters': {}, 'timeout': 30} for i in (1, 2, 3)]
    return Job.objects.create(name='Pipeline', handler='sql_pipeline', connection=connection, steps=steps)


class FakeDB:
    def __init__(self, failure=None, commit_failure=None, return_code=0, hook=None):
        self.calls = []; self.commits = 0; self.rollbacks = 0
        self.failure = failure; self.commit_failure = commit_failure; self.return_code = return_code; self.hook = hook
    def cursor(self):
        db = self
        class Cursor:
            description = [('__etl_step_return_code',)]
            def execute(self, sql, *values):
                db.calls.append((sql, values))
                if db.hook: db.hook()
                if len(db.calls) == db.failure: raise pyodbc.Error('42000', 'private SQL and parameters')
            def fetchone(self): return (db.return_code,)
            def nextset(self): return False
            def close(self): pass
        return Cursor()
    def commit(self):
        self.commits += 1
        if self.commits == self.commit_failure: raise pyodbc.Error('08S01', 'private connection error')
    def rollback(self): self.rollbacks += 1
    def close(self): pass


def execute(job, db):
    run, _ = enqueue(job)
    run.status = 'running'; run.save(update_fields=['status'])
    with patch('transferdata.python_jobs.pipeline.connect') as connection:
        connection.return_value.__enter__.return_value = db
        execute_pipeline(run, lambda **event: None)
    run.refresh_from_db()
    return run


@pytest.mark.parametrize('mode,commits', [('atomic', 1), ('per_step', 3)])
def test_commit_modes_and_order(pipeline, mode, commits):
    pipeline.transaction_mode = mode; pipeline.save()
    db = FakeDB()
    run = execute(pipeline, db)
    assert run.status == 'success'
    assert db.commits == commits and db.rollbacks == 0
    assert [s.status for s in run.step_runs.all()] == ['success']*3
    assert all(f'[proc_{i}]' in db.calls[i-1][0] for i in (1, 2, 3))


@pytest.mark.parametrize('mode,first', [('atomic', 'rolled_back'), ('per_step', 'success')])
def test_failure_does_not_run_later_steps_or_discard_committed_prefix(pipeline, mode, first):
    pipeline.transaction_mode = mode; pipeline.save()
    db = FakeDB(failure=2)
    run = execute(pipeline, db)
    assert run.status == 'failed' and len(db.calls) == 2
    assert list(run.step_runs.values_list('status', flat=True)) == [first, 'failed', 'pending']
    assert '42000' in run.message and 'private' not in run.message


@pytest.mark.parametrize('mode', ['atomic', 'per_step'])
def test_commit_failure_is_ambiguous_not_automatically_replayed(pipeline, mode):
    pipeline.transaction_mode = mode; pipeline.save()
    run = execute(pipeline, FakeDB(commit_failure=1))
    assert run.status == 'interrupted'
    assert run.step_runs.filter(status='unknown').exists()
    assert not run.step_runs.filter(status='success').exists()


@pytest.mark.parametrize('mode,first', [('atomic', 'rolled_back'), ('per_step', 'success')])
def test_stop_waits_for_current_procedure_and_respects_transaction_mode(pipeline, mode, first):
    pipeline.transaction_mode = mode; pipeline.save()
    db = FakeDB(hook=lambda: Run.objects.filter(job=pipeline, status='running').update(stop_requested=True))
    run = execute(pipeline, db)
    assert run.status == 'stopped' and len(db.calls) == 1
    assert list(run.step_runs.values_list('status', flat=True)) == [first, 'pending', 'pending']


def test_resume_uses_original_snapshot_and_preserves_successes(pipeline):
    pipeline.transaction_mode = 'per_step'; pipeline.save()
    old = execute(pipeline, FakeDB(failure=2))
    pipeline.steps[1]['procedure'] = 'edited_later'; pipeline.save()
    new, _ = enqueue(pipeline, resume_from=old, start_at='2')
    assert new.resume_of_id == old.pk
    assert new.snapshot['steps'][1]['procedure'] == 'proc_2'
    assert list(new.step_runs.values_list('status', flat=True)) == ['preserved', 'pending', 'pending']
    db = FakeDB()
    new.status = 'running'; new.save()
    with patch('transferdata.python_jobs.pipeline.connect') as connect:
        connect.return_value.__enter__.return_value = db
        execute_pipeline(new, lambda **event: None)
    assert len(db.calls) == 2 and '[proc_2]' in db.calls[0][0]


def test_resume_cannot_skip_failed_prefix_or_resume_atomic_transaction(pipeline):
    pipeline.transaction_mode = 'per_step'; pipeline.save()
    old = execute(pipeline, FakeDB(failure=1))
    with pytest.raises(ValidationError, match='незавершённые'):
        enqueue(pipeline, resume_from=old, start_at='2')
    old.snapshot['transaction_mode'] = 'atomic'; old.save()
    with pytest.raises(ValidationError, match='фиксацией по шагам'):
        enqueue(pipeline, resume_from=old, start_at='1')


def test_skipping_dependency_and_invalid_order_are_blocked(pipeline):
    with pytest.raises(ValidationError, match='отключён'):
        enqueue(pipeline, skip=['1'])
    steps = copy.deepcopy(pipeline.steps)
    steps.reverse()
    with pytest.raises(ValidationError, match='более позднего'):
        validate_steps(steps)
    assert not Run.objects.exists()


def test_optional_skip_only_changes_run_snapshot(pipeline):
    pipeline.steps[2]['depends_on'] = []; pipeline.save()
    run, _ = enqueue(pipeline, skip=['2'])
    assert run.step_runs.get(key='2').status == 'skipped'
    pipeline.refresh_from_db()
    assert pipeline.steps[1]['enabled'] is True


def test_procedure_parameters_are_bound_and_nonzero_return_code_fails(pipeline):
    step = pipeline.steps[0]
    step['parameters'] = {'value': "x'; DROP TABLE t;--"}
    sql, values = procedure_sql(step, 'DataWH')
    assert 'DROP TABLE' not in sql and values == ["x'; DROP TABLE t;--"]
    assert '@value = ?' in sql
    run = execute(pipeline, FakeDB(return_code=7))
    assert run.status == 'failed' and 'код 7' in run.message
    assert run.step_runs.get(key='1').status == 'failed'


def test_startup_recovery_and_queued_cancel_do_not_execute_sql(pipeline):
    run, _ = enqueue(pipeline)
    request_stop(run); run.refresh_from_db()
    assert run.status == 'stopped'
    new, _ = enqueue(pipeline)
    new.status = 'running'; new.save()
    new.step_runs.filter(key='1').update(status='running')
    recover_steps()
    assert new.step_runs.get(key='1').status == 'unknown'
    assert new.step_runs.get(key='2').status == 'pending'


def test_step_page_and_controls_are_permission_checked_and_post_only(pipeline, client, django_user_model):
    admin = django_user_model.objects.create_superuser('steps-admin')
    client.force_login(admin)
    start = client.get(reverse('python_jobs:pipeline_start', args=[pipeline.pk]))
    assert start.status_code == 200 and not Run.objects.exists()
    response = client.post(reverse('python_jobs:pipeline_start', args=[pipeline.pk]), {'confirmed': 'yes'})
    run = Run.objects.get()
    assert response.status_code == 302
    page = client.get(reverse('python_jobs:run', args=[run.pk]))
    assert page.status_code == 200 and 'Шаги запуска' in page.content.decode()
    stop_url = reverse('python_jobs:stop', args=[run.pk])
    assert client.get(stop_url).status_code == 405
    csrf = Client(enforce_csrf_checks=True); csrf.force_login(admin)
    assert csrf.post(stop_url).status_code == 403
    viewer = django_user_model.objects.create_user('steps-viewer', is_staff=True)
    client.force_login(viewer)
    assert client.post(stop_url).status_code == 403


def test_checkpoint_failure_after_commit_is_unknown(pipeline):
    pipeline.transaction_mode = 'per_step'; pipeline.save()
    original = StepRun.save
    failed = []
    def save(instance, *args, **kwargs):
        if instance.status == 'success' and not failed:
            failed.append(True)
            raise OperationalError('temporary service DB error')
        return original(instance, *args, **kwargs)
    with patch.object(StepRun, 'save', save):
        run = execute(pipeline, FakeDB())
    assert run.status == 'interrupted'
    assert run.step_runs.get(key='1').status == 'unknown'


def test_examples_are_created_without_execution_or_schedules(pipeline, client, django_user_model):
    client.force_login(django_user_model.objects.create_superuser('examples-admin'))
    url = reverse('python_jobs:pipeline_examples')
    assert client.get(url).status_code == 200
    with patch('transferdata.python_jobs.pipeline.connect') as connection:
        response = client.post(url, {'connection': pipeline.connection_id, 'confirmed': 'on'})
    assert response.status_code == 302
    examples = Job.objects.exclude(pk=pipeline.pk).order_by('pk')
    assert [len(j.steps) for j in examples] == [3, 7]
    assert all(j.transaction_mode == 'atomic' and not j.enabled for j in examples)
    assert not Run.objects.exists()
    connection.assert_not_called()
    assert client.post(url, {'connection': pipeline.connection_id, 'confirmed': 'on'}).status_code == 200
    assert Job.objects.count() == 3


def test_dependency_validation_and_resume_confirmation_prevent_side_effects(pipeline, client, django_user_model):
    client.force_login(django_user_model.objects.create_superuser('confirm-admin'))
    url = reverse('python_jobs:pipeline_start', args=[pipeline.pk])
    assert client.post(url, {'confirmed': 'yes', 'skip': '1'}).status_code == 200
    assert not Run.objects.exists()
    pipeline.transaction_mode = 'per_step'; pipeline.save()
    old = execute(pipeline, FakeDB(failure=2))
    url = reverse('python_jobs:resume', args=[old.pk])
    assert client.get(url).status_code == 200
    assert client.post(url, {'start_at': '2'}).status_code == 200
    assert Run.objects.count() == 1
    assert client.post(url, {'start_at': '2', 'confirmed': 'yes'}).status_code == 302
    assert Run.objects.count() == 2


@pytest.mark.parametrize('outcome', ['success', 'failed', 'stopped', 'interrupted'])
def test_parent_preserves_pipeline_terminal_result(pipeline, outcome):
    import io
    from unittest.mock import MagicMock
    from transferdata.python_jobs.services import execute_next
    run, _ = enqueue(pipeline)
    process = MagicMock()
    process.stdout = io.BytesIO(b'')
    process.poll.return_value = process.returncode = 0
    def spawn(*args, **kwargs):
        Run.objects.filter(pk=run.pk).update(status=outcome, message='Durable pipeline result')
        return process
    with patch('transferdata.python_jobs.services.subprocess.Popen', side_effect=spawn):
        assert execute_next()
    run.refresh_from_db()
    assert run.status == outcome and run.message == 'Durable pipeline result'


def test_failed_rollback_on_stop_is_not_reported_as_clean_stop(pipeline):
    class LostDB(FakeDB):
        def rollback(self): raise pyodbc.Error('08S01', 'connection lost')
    db = LostDB(hook=lambda: Run.objects.filter(job=pipeline, status='running').update(stop_requested=True))
    run = execute(pipeline, db)
    assert run.status == 'interrupted'
    assert run.step_runs.get(key='1').status == 'unknown'


def test_resume_cannot_bypass_changed_connection(pipeline):
    pipeline.transaction_mode = 'per_step'; pipeline.save()
    old = execute(pipeline, FakeDB(failure=2))
    pipeline.connection.server = 'different-server'
    pipeline.connection.save()
    with pytest.raises(ValidationError, match='подключение'):
        enqueue(pipeline, resume_from=old, start_at='2')


def test_yds_update_provisions_tasks_without_running_or_overwriting(pipeline, settings):
    from django.core.management import call_command
    settings.DEPLOYMENT = {'profile': 'yds'}
    with patch('pyodbc.drivers', return_value=['ODBC Driver 17 for SQL Server']), patch('transferdata.python_jobs.pipeline.connect') as sql:
        call_command('setup_sql_pipelines')
        job = Job.objects.get(name='Обновление DataWH')
        assert len(job.steps) == 7 and not job.enabled and job.transaction_mode == 'atomic'
        assert job.connection.server == 'MSK-DB2' and job.connection.database == 'DataWH'
        assert job.connection.authentication_type == 'WINDOWS'
        assert not job.connection.trust_server_certificate
        job.steps.reverse(); job.enabled = True; job.name = 'My renamed DataWH'; job.save()
        call_command('setup_sql_pipelines')
        job.refresh_from_db()
        assert job.steps[0]['id'] == 'plan_fact' and job.enabled
        assert Job.objects.count() == 3
        assert not Run.objects.exists()
        sql.assert_not_called()


def test_setup_retains_deleted_tasks_and_only_adds_missing_one(pipeline, settings):
    from django.core.management import call_command
    settings.DEPLOYMENT = {'profile': 'yds'}
    Job.objects.create(name='Бухгалтерия и ОС', handler='sql_pipeline', connection=pipeline.connection, steps=pipeline.steps, is_deleted=True)
    with patch('pyodbc.drivers', return_value=[]):
        call_command('setup_sql_pipelines')
    assert Job.objects.get(name='Бухгалтерия и ОС').is_deleted
    assert len(Job.objects.get(name='Обновление DataWH').steps) == 7
    assert not Run.objects.exists()


def test_post_migrate_provisions_only_current_yds_database(settings):
    from transferdata.python_jobs.provisioning import provision_yds
    settings.DEPLOYMENT = {'profile': 'yds'}
    with patch('transferdata.python_jobs.provisioning.call_command') as command:
        provision_yds(None)
        command.assert_called_once_with('setup_sql_pipelines')
        command.reset_mock()
        provision_yds(None, using='other')
        provision_yds(None, plan=[(None, True)])
        settings.DEPLOYMENT = {'profile': 'default'}
        provision_yds(None)
        command.assert_not_called()


def test_pipeline_real_connection_validation_uses_existing_profile(pipeline):
    run, _ = enqueue(pipeline)
    with pytest.raises(ValidationError):
        DatabaseConnection(**run.snapshot['connection']).full_clean()
    db = FakeDB()
    with patch('transferdata.erp.engine.connections.pyodbc.connect', return_value=db) as driver:
        execute_pipeline(run, lambda **event: None)
    run.refresh_from_db()
    assert run.status == 'success'
    assert driver.call_count == 1
    assert db.commits == 1 and len(db.calls) == 3


def test_connection_validation_failure_has_phase_and_no_private_values(pipeline):
    run, _ = enqueue(pipeline)
    error = ValidationError({'driver': ['private password or SQL text']})
    with patch('transferdata.python_jobs.pipeline.connect', side_effect=error):
        execute_pipeline(run, lambda **event: None)
    run.refresh_from_db()
    assert 'Подключение к SQL Server' in run.message and 'ODBC-драйвер' in run.message
    assert 'ещё не запускались' in run.message and 'private' not in run.message
    assert all(s.status == 'pending' for s in run.step_runs.all())


def test_sql_native_error_is_explained_without_leaking_driver_values(pipeline):
    class MissingObject(FakeDB):
        def cursor(self):
            cursor = super().cursor()
            original = cursor.execute
            def execute(sql, *values):
                original(sql, *values)
                if len(self.calls) == 3:
                    raise pyodbc.ProgrammingError('42000', "[SQL Server]Invalid object 'private_customer_table' secret=hidden (208) (SQLExecDirectW)")
            cursor.execute = execute
            return cursor
    run = execute(pipeline, MissingObject())
    assert run.status == 'failed' and run.completed == 2 and run.percent == 66
    assert 'SQL Server 208' in run.message and 'Не найдена таблица' in run.message
    assert 'Step 3' in run.message and 'private' not in run.message and 'hidden' not in run.message
    assert 'SQL Server 208' in run.step_runs.get(key='3').message


def test_unknown_sql_text_is_not_exposed():
    from transferdata.python_jobs.sql_diagnostics import native_errors
    assert native_errors(pyodbc.Error('42000', "secret customer row (987654) (SQLExecDirectW)")) == ''
    assert 'параметр' in native_errors(pyodbc.Error('42000', 'private argument (201) (SQLExecDirectW)'))
