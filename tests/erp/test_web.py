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


def test_connection_certificate_option_can_be_saved(client, administrator_user, job):
    client.force_login(administrator_user)
    connection = job.source_connection
    assert not connection.trust_server_certificate
    data = {'name': connection.name, 'server': connection.server, 'port': connection.port,
            'database': connection.database, 'authentication_type': 'WINDOWS', 'driver': 'ODBC Driver 17 for SQL Server',
            'connection_timeout': 10, 'query_timeout': 300, 'role': 'SOURCE', 'is_active': 'on',
            'trust_server_certificate': 'on'}
    url = reverse('erp:connection_edit', args=[connection.pk])
    assert client.post(url, data).status_code == 302
    connection.refresh_from_db()
    assert connection.trust_server_certificate
    data.pop('trust_server_certificate')
    assert client.post(url, data).status_code == 302
    connection.refresh_from_db()
    assert not connection.trust_server_certificate

def test_history_filters(client, observer, job):
    ETLExecution.objects.create(job=job, status='SUCCESS')
    ETLExecution.objects.create(job=job, status='FAILED')
    client.force_login(observer)
    response = client.get('/erp/etl/executions/', {'status': 'FAILED', 'job': job.pk, 'trigger': 'MANUAL'})
    assert len(response.context['page']) == 1
    assert client.get('/erp/etl/executions/', {'from': 'bad'}).status_code == 400


def test_job_groups_filter_and_form_support(client, administrator_user, job):
    from transferdata.web.panel.models import JobGroup
    job.group = JobGroup.objects.create(name='Справочники')
    job.save()
    client.force_login(administrator_user)
    response = client.get(reverse('erp:jobs'), {'group': job.group_id})
    assert list(response.context['items']) == [job]
    assert not list(client.get(reverse('erp:jobs'), {'group': '__ungrouped__'}).context['items'])
    response = client.get(reverse('erp:job_edit', args=[job.pk]))
    assert 'data-searchable' in response.content.decode()
    assert response.context['form']['group'].value() == job.group_id


def test_bulk_jobs_group_archive_restore_delete(client, administrator_user, job):
    from transferdata.web.panel.models import JobGroup
    group = JobGroup.objects.create(name='Общая группа')
    client.force_login(administrator_user)
    url = reverse('erp:job_bulk')
    payload = {'selected': [job.pk], 'operation': 'group', 'group': group.pk}
    assert client.post(url, payload).status_code == 200
    job.refresh_from_db()
    assert job.group is None
    client.post(url, {**payload, 'confirmed': 'yes'})
    job.refresh_from_db()
    assert job.group == group
    execution = ETLExecution.objects.create(job=job, status='SUCCESS')
    client.post(url, {**payload, 'operation': 'archive', 'confirmed': 'yes'})
    job.refresh_from_db()
    assert job.archived and not job.schedule_enabled
    assert not client.get(reverse('erp:jobs')).context['items']
    assert list(client.get(reverse('erp:jobs'), {'state': 'archived'}).context['items']) == [job]
    client.post(url, {**payload, 'operation': 'restore', 'confirmed': 'yes'})
    job.refresh_from_db()
    assert not job.archived
    client.post(url, {**payload, 'operation': 'delete', 'confirmed': 'yes'})
    job.refresh_from_db()
    assert job.is_deleted
    assert ETLExecution.objects.filter(pk=execution.pk).exists()
    assert client.get(reverse('erp:job_edit', args=[job.pk])).status_code == 404


def test_bulk_active_protection_and_observer_permission(client, administrator_user, observer, job):
    url = reverse('erp:job_bulk')
    payload = {'selected': [job.pk], 'operation': 'delete', 'confirmed': 'yes'}
    client.force_login(observer)
    assert client.post(url, payload).status_code == 403
    client.force_login(administrator_user)
    ETLExecution.objects.create(job=job)
    client.post(url, payload)
    job.refresh_from_db()
    assert not job.is_deleted
    client.post(url, {**payload, 'operation': 'restart'})
    assert job.executions.count() == 1


def test_grouped_display_counts_jobs_with_different_latest_statuses(client, observer, job):
    from transferdata.web.panel.models import JobGroup
    group = JobGroup.objects.create(name='Одна группа')
    job.group = group
    job.save()
    other = type(job).objects.create(name='Another', group=group,
        source_connection=job.source_connection, target_connection=job.target_connection,
        source_table='dbo.Other', target_table='Other')
    ETLExecution.objects.create(job=job, status='SUCCESS')
    ETLExecution.objects.create(job=other, status='FAILED')
    client.force_login(observer)
    response = client.get(reverse('erp:jobs'))
    sections = response.context['job_sections']
    assert len(sections) == 1
    assert sections[0]['count'] == 2
    assert {j.pk for j in sections[0]['jobs']} == {job.pk, other.pk}
