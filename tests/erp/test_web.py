import pytest
from django.urls import reverse
from django.test import Client
from transferdata.erp.control.models import ETLExecution, ETLAuditLog

pytestmark = pytest.mark.django_db

@pytest.mark.parametrize('path', ['/erp/', '/erp/connections/', '/erp/etl/jobs/', '/erp/etl/executions/'])
def test_login_required(client, path):
    assert client.get(path).status_code == 302

@pytest.mark.parametrize('action', ['job_run', 'job_disable', 'job_edit', 'connection_test', 'connection_edit'])
def test_observer_cannot_mutate(client, observer, job, action):
    client.force_login(observer)
    pk = job.source_connection_id if action.startswith('connection') else job.pk
    assert client.post(reverse("erp:" + action, args=[pk]), {}).status_code == 403
    assert ETLExecution.objects.count() == 0

def test_all_pages_render(client, observer, job):
    execution = ETLExecution.objects.create(job=job)
    client.force_login(observer)
    for path in ['/erp/', '/erp/connections/', '/erp/etl/jobs/', f'/erp/etl/jobs/{job.pk}/', '/erp/etl/executions/', f'/erp/etl/executions/{execution.pk}/']:
        response = client.get(path)
        assert response.status_code == 200, path
    response = client.get(reverse('erp:execution_detail', args=[execution.pk]), HTTP_HX_REQUEST='true')
    assert b'hx-trigger="every 3s"' in response.content
    assert b'<html' not in response.content

def test_launch_enqueues_only(client, administrator_user, job):
    client.force_login(administrator_user)
    assert client.get(reverse('erp:job_run', args=[job.pk])).status_code == 405
    response = client.post(reverse('erp:job_run', args=[job.pk]))
    assert response.status_code == 302
    assert job.executions.get().status == 'PENDING'
    client.post(reverse('erp:job_run', args=[job.pk]))
    assert job.executions.count() == 1

def test_csrf(administrator_user, job):
    client = Client(enforce_csrf_checks=True)
    client.force_login(administrator_user)
    assert client.post(reverse('erp:job_run', args=[job.pk])).status_code == 403

def test_job_form_audit(client, administrator_user, job):
    client.force_login(administrator_user)
    data = {'name': 'New job', 'source_connection': job.source_connection_id, 'target_connection': job.target_connection_id, 'source_type': 'SQL_QUERY', 'sql_query': 'SELECT 1 AS id', 'target_schema': 'dbo', 'target_table': 'new', 'load_mode': 'FULL', 'batch_size': 1000, 'command_timeout': 30, 'is_active': 'on'}
    response = client.post(reverse('erp:job_create'), data)
    assert response.status_code == 302
    assert ETLAuditLog.objects.get().actor == administrator_user
    assert job.__class__.objects.get(name='New job').created_by == administrator_user

def test_history_filters(client, observer, job):
    ETLExecution.objects.create(job=job, status='SUCCESS')
    ETLExecution.objects.create(job=job, status='FAILED')
    client.force_login(observer)
    response = client.get('/erp/etl/executions/', {'status': 'FAILED', 'job': job.pk, 'trigger': 'MANUAL'})
    assert len(response.context['page']) == 1
    assert client.get('/erp/etl/executions/', {'from': 'bad'}).status_code == 400
