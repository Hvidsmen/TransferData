import pytest
from django.urls import reverse
from django.contrib.auth import get_user_model
from transferdata.web.panel.models import Connection, Job, JobGroup, Run
from transferdata.erp.control.models import DatabaseConnection, ETLJob, ETLExecution
from transferdata.python_jobs.models import Job as PythonJob, Run as PythonRun

pytestmark = pytest.mark.django_db


@pytest.fixture
def portal_jobs(client):
    user = get_user_model().objects.create_superuser('overview-admin', password='test-password')
    client.force_login(user)
    group = JobGroup.objects.create(name='Общие задания')
    td = Job.objects.create(name='SQL task', group=group, connection=Connection.objects.create(name='SQL'))
    Run.objects.create(job=td, action='run', status='success', snapshot={})
    Run.objects.create(job=td, action='check', status='failed', snapshot={})
    source = DatabaseConnection.objects.create(name='Source', server='vm-dwh', database='DataWH', authentication_type='WINDOWS')
    erp = ETLJob.objects.create(name='1C task', group=group, source_connection=source, target_connection=source, target_table='test')
    ETLExecution.objects.create(job=erp, status='RUNNING')
    py = PythonJob.objects.create(name='Python task', group=group, connection=source)
    PythonRun.objects.create(job=py, status='failed', snapshot={})
    PythonJob.objects.create(name='Ungrouped', connection=source)
    PythonJob.objects.create(name='Deleted', connection=source, is_deleted=True)
    return client, user


def test_home_groups_all_workers_and_last_operational_status(portal_jobs):
    client, _ = portal_jobs
    response = client.get(reverse('etl_portal'))
    assert response.status_code == 200
    assert response.context['job_total'] == 4
    groups = response.context['portal_groups']
    assert [g['name'] for g in groups] == ['Общие задания', 'Без группы']
    assert groups[0]['count'] == 3
    workers = {w['key']: w for w in groups[0]['workers']}
    assert set(workers) == {'sql', 'onec', 'python'}
    assert workers['sql']['jobs'][0]['status'] == 'success'  # connection check must not hide transfer success
    assert workers['onec']['jobs'][0]['status'] == 'running'
    assert workers['python']['jobs'][0]['status'] == 'failed'
    body = response.content.decode()
    assert 'portal-live-jobs' in body and 'portal-task-grid' in body and 'Deleted' not in body
    assert body.index('portal-live-jobs') < body.index('ПОДСИСТЕМА 01')


def test_home_filters_worker_status_and_group_text(portal_jobs):
    client, _ = portal_jobs
    response = client.get(reverse('etl_portal'), {'worker': 'python', 'status': 'failed', 'q': 'ОБЩИЕ'})
    assert response.context['job_shown'] == 1
    assert response.context['job_total'] == 4
    groups = response.context['portal_groups']
    assert groups[0]['workers'][0]['jobs'][0]['name'] == 'Python task'
    assert not client.get(reverse('etl_portal'), {'q': 'no-match'}).context['portal_groups']


def test_home_respects_job_and_history_permissions(portal_jobs):
    from django.contrib.auth.models import Permission
    client, _ = portal_jobs
    user = get_user_model().objects.create_user('observer-home', password='test-password', is_staff=True)
    user.user_permissions.add(Permission.objects.get(content_type__app_label='python_jobs', codename='view_job'))
    client.force_login(user)
    response = client.get(reverse('etl_portal'))
    workers = [w for g in response.context['portal_groups'] for w in g['workers']]
    assert 'sql' not in {w['key'] for w in workers}
    python = next(w for w in workers if w['key'] == 'python')
    assert all(j['status'] == 'restricted' and not j['run_url'] for j in python['jobs'])
    assert client.get(reverse('python_jobs:history')).status_code == 403
