from datetime import time
from unittest.mock import patch
import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.core.management import call_command
from transferdata.erp.control.models import ETLExecution, ETLAuditLog
from transferdata.erp.control.services import enqueue, claim, execute, recover_interrupted
from transferdata.erp.engine.validators import identifier, table_name, read_query

pytestmark = pytest.mark.django_db

def test_creation_and_validation(job):
    job.full_clean()
    assert job.batch_size == 10000
    assert job.source_table == 'dbo._Reference1'

@pytest.mark.parametrize('field,value', [('batch_size', 0), ('batch_size', 100001), ('command_timeout', 0), ('target_schema', 'dbo]; DROP TABLE x--'), ('source_table', 'db.dbo.t'), ('target_table', '')])
def test_invalid_configuration(job, field, value):
    setattr(job, field, value)
    with pytest.raises(ValidationError): job.full_clean()

def test_query_and_roles(job):
    job.source_type, job.sql_query = 'SQL_QUERY', 'WITH a AS (SELECT 1 AS x) SELECT x FROM a'
    job.full_clean()
    job.source_connection.role = 'TARGET'
    with pytest.raises(ValidationError): job.full_clean()

def test_same_database_rejected(job):
    job.target_connection.server = job.source_connection.server
    job.target_connection.database = job.source_connection.database
    with pytest.raises(ValidationError): job.full_clean()

@pytest.mark.parametrize('query', ['DELETE FROM x', 'SELECT * INTO x FROM y', 'SELECT 1; SELECT 2', 'EXEC p', "SELECT * FROM OPENQUERY(s, 'select 1')"])
def test_query_restrictions(query):
    with pytest.raises(ValidationError): read_query(query)

def test_enqueue_unique_and_claim(job):
    execution = enqueue(job)
    assert execution.status == 'PENDING'
    with pytest.raises(ValidationError): enqueue(job)
    # Constraint also protects direct DB writes, independently of services.
    with pytest.raises(IntegrityError), transaction.atomic():
        ETLExecution.objects.create(job=job)
    running = claim()
    assert running.pk == execution.pk and running.status == 'RUNNING'
    assert claim() is None
    with patch('transferdata.erp.control.services.run') as run:
        run.side_effect = lambda j, progress: progress(17, 'Публикация')
        execute(running)
    execution.refresh_from_db()
    assert execution.status == 'SUCCESS' and execution.rows_loaded == 17
    assert execution.duration_seconds >= 0
    assert execution.logs.count() == 2
    assert enqueue(job).pk != execution.pk
    assert ETLExecution.objects.count() == 2

@pytest.mark.parametrize('mode,active', [('INCREMENTAL', True), ('FULL', False)])
def test_unrunnable(job, mode, active):
    job.load_mode, job.is_active = mode, active
    with pytest.raises(ValidationError): enqueue(job)

def test_error_does_not_leak_secret(job):
    enqueue(job)
    execution = claim()
    with patch('transferdata.erp.control.services.run', side_effect=RuntimeError('PWD=very-secret SQL secret')):
        execute(execution)
    execution.refresh_from_db()
    assert execution.status == 'FAILED'
    assert 'very-secret' not in execution.error_message
    assert all('very-secret' not in log.message for log in execution.logs.all())

def test_recovery_does_not_retry(job):
    enqueue(job)
    execution = claim()
    recover_interrupted()
    execution.refresh_from_db()
    assert execution.status == 'FAILED'
    assert 'могла завершиться' in execution.error_message
    assert claim() is None

def test_schedule_idempotence(job):
    job.schedule_enabled, job.schedule_time = True, time(0)
    job.save()
    call_command('enqueue_scheduled_jobs')
    call_command('enqueue_scheduled_jobs')
    assert job.executions.count() == 1
    execution = claim()
    with patch('transferdata.erp.control.services.run'):
        execute(execution)
    call_command('enqueue_scheduled_jobs')
    assert job.executions.count() == 1
    assert execution.trigger_type == 'SCHEDULED'

def test_schedule_future_disabled_and_incremental(job):
    from django.utils import timezone
    from datetime import datetime
    job.schedule_enabled, job.schedule_time = True, time(23, 59)
    job.save()
    with patch('transferdata.erp.control.services.timezone.localtime', return_value=timezone.make_aware(datetime(2026, 1, 1, 12))):
        call_command('enqueue_scheduled_jobs')
    assert job.executions.count() == 0
    job.schedule_time, job.load_mode = time(0), 'INCREMENTAL'
    job.save()
    call_command('enqueue_scheduled_jobs')
    assert job.executions.count() == 0

def test_worker_once(job):
    execution = enqueue(job)
    with patch('transferdata.erp.control.services.run'):
        call_command('etl_worker', once=True)
    execution.refresh_from_db()
    assert execution.status == 'SUCCESS'

def test_cancel_pending_only(job):
    from transferdata.erp.control.services import cancel_pending
    execution = enqueue(job)
    assert cancel_pending(execution)
    assert claim() is None
    execution.refresh_from_db()
    assert execution.status == 'CANCELLED'
    running = enqueue(job)
    claimed = claim()
    assert not cancel_pending(claimed)

def test_stale_heartbeat_detected(job):
    from datetime import timedelta
    from django.utils import timezone
    enqueue(job)
    execution = claim()
    assert not execution.heartbeat_stale
    execution.heartbeat = timezone.now() - timedelta(seconds=61)
    assert execution.heartbeat_stale
    execution.status = 'FAILED'
    assert not execution.heartbeat_stale


def test_sql_names_allow_readable_target_names_and_report_exact_field(job):
    job.target_table = 'Справочник.Бизнес регионы-ERP'
    job.full_clean()
    assert identifier(job.target_table) == '[Справочник.Бизнес регионы-ERP]'
    job.target_table = 'bad]; DROP TABLE x--'
    with pytest.raises(ValidationError) as exc:
        job.full_clean()
    assert 'target_table' in exc.value.message_dict
    assert '128' not in str(exc.value.message_dict['target_table'])
    job.target_table = 'x' * 129
    with pytest.raises(ValidationError) as exc:
        job.full_clean()
    assert '129' in str(exc.value.message_dict['target_table'])
    job.target_table = 'valid'
    job.source_type = 'TABLE'
    job.source_table = 'db.dbo.table'
    with pytest.raises(ValidationError) as exc:
        job.full_clean()
    assert 'source_table' in exc.value.message_dict
