from datetime import timedelta
from django.utils import timezone
import pytest
from transferdata.web.panel.operations import worker_health, worker_heartbeat, next_launch
from transferdata.web.panel.templatetags.ux import run_state, failure_help
from transferdata.web.panel.models import Connection, Job, Run
from transferdata.erp.control.models import DatabaseConnection, ETLJob, ETLExecution, WorkerLease
from transferdata.web.panel.monitoring import erp_jobs, transferdata_jobs

pytestmark = pytest.mark.django_db


def test_health_requires_fresh_aware_telemetry_and_pauses_explicitly(settings, tmp_path):
    settings.STATE_DIR = tmp_path
    assert all(h['state'] == 'unknown' for h in worker_health())
    (tmp_path / 'sql-worker.heartbeat').write_text(timezone.now().isoformat())
    WorkerLease.objects.create(pk=1, owner='test', heartbeat=timezone.now() - timedelta(minutes=3))
    observed = {h['key']: h for h in worker_health()}
    assert observed['sql']['online'] and not observed['onec']['online']
    (tmp_path / 'sql-worker.heartbeat').write_text((timezone.now() + timedelta(days=1)).isoformat())
    (tmp_path / 'python-worker.heartbeat').write_text('malformed')
    assert all(not h['online'] for h in worker_health())
    (tmp_path / 'maintenance.flag').touch()
    assert all(h['state'] == 'paused' for h in worker_health())


def test_sql_signal_lifecycle_even_while_the_worker_is_busy(settings, tmp_path):
    settings.STATE_DIR = tmp_path
    with worker_heartbeat('sql'):
        assert (tmp_path / 'sql-worker.heartbeat').exists()
        assert next(h for h in worker_health() if h['key'] == 'sql')['online']
    assert not (tmp_path / 'sql-worker.heartbeat').exists()


def test_last_success_survives_new_error_and_due_time_is_truthful(django_user_model):
    user = django_user_model.objects.create_superuser('ux-admin')
    now = timezone.now()
    job = Job.objects.create(name='UX task', connection=Connection.objects.create(name='UX connection'), enabled=True,
                             next_run_at=now - timedelta(minutes=5))
    completed = Run.objects.create(job=job, action='run', status='success', snapshot={}, finished_at=now - timedelta(hours=2))
    Run.objects.create(job=job, action='run', status='failed', snapshot={})
    item = transferdata_jobs(user)[0]
    assert item['last_success'] == completed.finished_at and item['status'] == 'failed'
    assert item['overdue'] and item['attention']
    job.enabled = False; job.save()
    assert next_launch(job, 'transferdata') is None


def test_erp_next_time_respects_the_once_per_day_schedule_key():
    connection = DatabaseConnection.objects.create(name='UX ERP', server='demo', database='ERP')
    now = timezone.localtime()
    job = ETLJob.objects.create(name='UX ERP task', source_connection=connection, target_connection=connection,
        target_table='demo', schedule_enabled=True, schedule_cron='0 8 * * *')
    ETLExecution.objects.create(job=job, status='SUCCESS', schedule_key=f'{job.pk}:{now.date().isoformat()}')
    item = erp_jobs([job.pk])[0]
    assert timezone.localtime(item['next_run']).date() == now.date() + timedelta(days=1)
    assert not item['overdue']


def test_queue_without_worker_is_not_labelled_as_running(settings, tmp_path):
    settings.STATE_DIR = tmp_path
    job = Job.objects.create(name='Waiting', connection=Connection.objects.create(name='Waiting connection'))
    run = Run.objects.create(job=job, action='run', snapshot={})
    facts = run_state({}, run, 'transferdata')
    assert 'Ожидает обработчика' in facts['phase'] and facts['state'] == 'queued'
    assert facts['wait_seconds'] >= 0
    assert 'нулевой символ' in failure_help('Обнаружен U+0000')['error_title']
