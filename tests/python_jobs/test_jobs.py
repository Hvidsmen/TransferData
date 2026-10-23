from datetime import datetime, timedelta, timezone as tz
from pathlib import Path
from unittest.mock import patch
import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.utils import timezone
from transferdata.erp.control.models import DatabaseConnection
from transferdata.python_jobs.models import Job, Run, script_path
from transferdata.python_jobs.services import enqueue, schedule_due, execute_next

pytestmark = pytest.mark.django_db


@pytest.fixture
def python_job(settings, tmp_path):
    settings.STATE_DIR = tmp_path
    connection = DatabaseConnection.objects.create(name='DWH', server='vm-dwh', database='DataWH', authentication_type='WINDOWS', role='TARGET')
    return Job.objects.create(name='Headers', connection=connection, parameters={'date_from': '2023-01-01'})


def test_enqueue_snapshot_is_stable_and_duplicate_prevented(python_job):
    run, created = enqueue(python_job)
    assert created
    assert run.snapshot['connection']['database'] == 'DataWH'
    assert 'created_at' not in run.snapshot['connection']
    python_job.parameters = {'date_from': '2024-01-01'}
    python_job.save()
    second, created = enqueue(python_job)
    assert second.pk == run.pk and not created
    run.refresh_from_db()
    assert run.snapshot['parameters']['date_from'] == '2023-01-01'


def test_maintenance_and_archive_block_execution(python_job, settings):
    (settings.STATE_DIR / 'maintenance.flag').touch()
    with pytest.raises(ValidationError, match='обновляется'):
        enqueue(python_job)
    (settings.STATE_DIR / 'maintenance.flag').unlink()
    python_job.archived = True; python_job.save()
    with pytest.raises(ValidationError, match='архиве'):
        enqueue(python_job)


def test_due_schedule_does_not_overlap(python_job):
    now = datetime(2026, 10, 23, 8, tzinfo=tz.utc)
    python_job.enabled = True; python_job.next_run_at = now - timedelta(seconds=1); python_job.save()
    schedule_due(now)
    assert Run.objects.count() == 1
    python_job.refresh_from_db()
    assert python_job.next_run_at > now
    schedule_due(now + timedelta(days=1))
    assert Run.objects.count() == 1


def test_scripts_cannot_escape_root(python_job, settings, tmp_path):
    root = settings.STATE_DIR / 'python-scripts'; root.mkdir()
    (root / 'ok.py').write_text('print("ok")')
    (tmp_path / 'outside.py').write_text('pass')
    assert script_path('ok.py') == root / 'ok.py'
    for path in ('../outside.py', str(tmp_path / 'outside.py'), 'missing.py', 'ok.txt'):
        with pytest.raises(ValidationError): script_path(path)
    (root / 'link.py').symlink_to(tmp_path / 'outside.py')
    with pytest.raises(ValidationError): script_path('link.py')


@pytest.mark.parametrize('mode,status', [('success', 'success'), ('error', 'failed'), ('timeout', 'failed')])
def test_real_script_subprocess_exit_timeout_and_unicode(python_job, settings, mode, status):
    root = settings.STATE_DIR / 'python-scripts'; root.mkdir()
    script = root / 'test.py'
    scripts = {
        'success': 'import os,json\nprint("Привет",json.loads(os.environ["ETL_JOB_PARAMETERS"])["value"])\n',
        'error': 'print("diagnostic")\nraise SystemExit(7)\n',
        'timeout': 'import time\nprint("started",flush=True)\ntime.sleep(30)\n'}
    script.write_text(scripts[mode], encoding='utf-8')
    python_job.handler = 'script'; python_job.script = 'test.py'; python_job.timeout = 1 if mode == 'timeout' else 10
    python_job.parameters = {'value': 'Параметр'}; python_job.save()
    run, _ = enqueue(python_job)
    assert execute_next()
    run.refresh_from_db()
    assert run.status == status and run.finished_at and run.seconds < 15
    if mode == 'success': assert 'Привет Параметр' in run.output
    elif mode == 'error': assert 'diagnostic' in run.output
    else: assert 'Превышено' in run.message and 'started' in run.output


def test_setup_is_idempotent_and_does_not_run_jobs(python_job):
    python_job.delete()
    call_command('setup_python_jobs')
    call_command('setup_python_jobs')
    assert Job.objects.count() == 1 and Run.objects.count() == 0
    assert not Job.objects.get().enabled


def test_connection_snapshot_can_be_validated_without_losing_identity(python_job):
    from transferdata.python_jobs.handlers import connection_from_snapshot
    config = connection_from_snapshot(python_job.snapshot()['connection'])
    config.full_clean()
    assert config.pk == python_job.connection_id


@pytest.mark.parametrize('success', [True, False])
def test_builtin_worker_records_real_progress_and_requires_result(python_job, success):
    import io
    import json
    from unittest.mock import MagicMock
    from transferdata.python_jobs.services import PREFIX
    progress = {'completed': 1, 'total': 3, 'rows': 12, 'message': 'Обработан один документ'}
    events = [PREFIX + json.dumps(progress)]
    if success:
        events.append(PREFIX + json.dumps({'result': True, 'rows': 12}))
    else:
        events.append('CommandError: last document rolled back')
    process = MagicMock()
    process.stdout = io.BytesIO(('\n'.join(events) + '\n').encode())
    process.poll.return_value = process.returncode = 0 if success else 1
    run, _ = enqueue(python_job)
    with patch('transferdata.python_jobs.services.subprocess.Popen', return_value=process):
        assert execute_next()
    run.refresh_from_db()
    assert run.status == ('success' if success else 'failed')
    assert run.completed == 1 and run.total == 3 and run.rows == 12
    assert 'Обработан один документ' in run.output


def test_worker_restart_marks_abandoned_run_interrupted(python_job, settings):
    run = Run.objects.create(job=python_job, status='running', snapshot=python_job.snapshot())
    call_command('pythonworker', once=True)
    run.refresh_from_db()
    assert run.status == 'interrupted' and run.finished_at is not None
    assert not (settings.STATE_DIR / 'python-worker.heartbeat').exists()


def test_script_output_is_bounded(python_job, settings):
    root = settings.STATE_DIR / 'python-scripts'; root.mkdir()
    (root / 'noisy.py').write_text('print("x"*200000)\n')
    python_job.handler = 'script'; python_job.script = 'noisy.py'; python_job.parameters = {}; python_job.save()
    run, _ = enqueue(python_job)
    execute_next()
    run.refresh_from_db()
    assert run.status == 'success' and len(run.output) <= 65536
