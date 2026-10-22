import pytest
from django.urls import reverse
from transferdata.erp.control.models import ETLJob, ETLExecution
from transferdata.web.panel.monitoring import erp_jobs

pytestmark = pytest.mark.django_db


def test_erp_monitor_has_every_undeleted_job_once_and_its_latest_execution(job, client, administrator_user):
    ETLExecution.objects.create(job=job, status='FAILED')
    last = ETLExecution.objects.create(job=job, status='RUNNING', rows_loaded=73)
    never = ETLJob.objects.create(name='Не запускалось', source_connection=job.source_connection,
        target_connection=job.target_connection, source_table='dbo.test', target_table='new')
    ETLJob.objects.create(name='Удалено', is_deleted=True, source_connection=job.source_connection,
        target_connection=job.target_connection, source_table='dbo.test', target_table='deleted')
    rows = {row['name']: row for row in erp_jobs()}
    assert len(rows) == 2
    assert rows[job.name]['status'] == 'running' and rows[job.name]['rows'] == 73
    assert rows[job.name]['run_url'] == reverse('erp:execution_detail', args=[last.pk])
    assert rows[never.name]['status'] == 'never'
    client.force_login(administrator_user)
    response = client.get(reverse('erp:dashboard'))
    assert response.status_code == 200
    assert b'live-job-monitor' in response.content
    assert len(response.context['monitor_jobs']) == 2
