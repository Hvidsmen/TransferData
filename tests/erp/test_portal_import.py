import json
import pytest
from django.core import serializers
from django.core.management import call_command, CommandError
from transferdata.erp.control.models import DatabaseConnection, ETLJob, ETLExecution

pytestmark = pytest.mark.django_db


def test_import_preserves_jobs_and_history_without_restarting_jobs(job, administrator_user, tmp_path):
    job.schedule_enabled = True
    job.created_by = administrator_user
    job.save()
    execution = ETLExecution.objects.create(job=job, status='RUNNING')
    payload = serializers.serialize('json', [job.source_connection, job.target_connection, job, execution],
                                    use_natural_foreign_keys=True)
    path = tmp_path / 'erp.json'
    path.write_text(payload, encoding='utf-8')
    ETLExecution.objects.all().delete()
    ETLJob.objects.all().delete()
    DatabaseConnection.objects.all().delete()
    call_command('import_erp_export', str(path))
    restored = ETLJob.objects.get(pk=job.pk)
    assert restored.source_table == job.source_table
    assert restored.created_by == administrator_user
    assert not restored.schedule_enabled
    assert ETLExecution.objects.get(pk=execution.pk).status == 'FAILED'
    assert path.read_text() == payload
    with pytest.raises(CommandError):
        call_command('import_erp_export', str(path))
    assert ETLJob.objects.count() == 1


def test_import_rejects_auth_records(tmp_path):
    path = tmp_path / 'invalid.json'
    path.write_text(json.dumps([{'model': 'auth.user', 'pk': 1, 'fields': {}}]))
    with pytest.raises(CommandError, match='Only ERP'):
        call_command('import_erp_export', str(path))


def test_subsystems_share_login_but_keep_job_lists_separate(client, administrator_user, job):
    from transferdata.web.panel.models import Connection, Job
    connection = Connection.objects.create(name='Postgres_ONLY', target_host='pg.test', target_database='analytics')
    Job.objects.create(name='Postgres_ONLY', connection=connection, source_table='source', target_table='target')
    client.force_login(administrator_user)
    response = client.get('/jobs/')
    assert response.status_code == 200
    assert b'Postgres_ONLY' in response.content
    assert job.name.encode() not in response.content
    response = client.get('/erp/etl/jobs/')
    assert response.status_code == 200
    assert job.name.encode() in response.content
    assert b'Postgres_ONLY' not in response.content


def test_maintenance_does_not_claim_pending_erp_work(job, tmp_path, settings):
    from transferdata.erp.control.services import enqueue
    from django.core.exceptions import ValidationError
    settings.STATE_DIR = tmp_path
    execution = enqueue(job)
    (tmp_path / 'maintenance.flag').touch()
    call_command('etl_worker', once=True)
    execution.refresh_from_db()
    assert execution.status == 'PENDING'
    assert (tmp_path / 'paused-erp').exists()
    with pytest.raises(ValidationError):
        enqueue(job)
