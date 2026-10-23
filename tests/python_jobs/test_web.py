import pytest
from django.urls import reverse
from transferdata.python_jobs.models import Job, Run
from transferdata.web.panel.models import JobGroup
from transferdata.erp.control.models import DatabaseConnection

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_client(client, django_user_model):
    user = django_user_model.objects.create_superuser('python-admin', password='test-password')
    client.force_login(user)
    return client


@pytest.fixture
def python_job():
    connection = DatabaseConnection.objects.create(name='DWH', server='vm-dwh', database='DataWH', authentication_type='WINDOWS')
    return Job.objects.create(name='Заголовки', connection=connection, group=JobGroup.objects.create(name='Коммерческие предложения'))


def test_all_pages_share_design_and_live_status(admin_client, python_job):
    run = Run.objects.create(job=python_job, snapshot=python_job.snapshot())
    for name, kwargs in [('overview', {}), ('jobs', {}), ('history', {}), ('add', {}), ('edit', {'pk': python_job.pk}), ('run', {'pk': run.pk})]:
        response = admin_client.get(reverse('python_jobs:' + name, kwargs=kwargs))
        assert response.status_code == 200
        body = response.content.decode()
        assert 'panel/studio.css' in body and 'Python обработчик' in body
        if name in ('overview', 'history', 'run'): assert 'data-live' in body
    assert 'Коммерческие предложения' in admin_client.get(reverse('python_jobs:jobs')).content.decode()
    assert 'panel/schedule.js' in admin_client.get(reverse('python_jobs:add')).content.decode()


def test_permissions_and_post_only_execution(client, django_user_model, python_job):
    viewer = django_user_model.objects.create_user('viewer-python', password='test-password', is_staff=True)
    client.force_login(viewer)
    assert client.get(reverse('python_jobs:jobs')).status_code == 403
    assert client.post(reverse('python_jobs:execute', args=[python_job.pk])).status_code == 403
    assert Run.objects.count() == 0


def test_enqueue_bulk_archive_delete_preserves_history(admin_client, python_job):
    response = admin_client.post(reverse('python_jobs:execute', args=[python_job.pk]))
    assert response.status_code == 302
    assert Run.objects.count() == 1
    data = {'operation': 'delete', 'selected': [python_job.pk], 'confirmed': 'yes'}
    admin_client.post(reverse('python_jobs:bulk'), data)
    python_job.refresh_from_db(); assert not python_job.is_deleted
    Run.objects.update(status='success')
    admin_client.post(reverse('python_jobs:bulk'), data)
    python_job.refresh_from_db(); assert python_job.is_deleted and not python_job.enabled
    assert Run.objects.count() == 1


def test_python_schedule_widget_and_group_bulk_assignment(admin_client, python_job):
    group = JobGroup.objects.create(name='Обработчики')
    response = admin_client.post(reverse('python_jobs:bulk'), {'operation': 'group', 'group': group.pk,
        'selected': [python_job.pk], 'confirmed': 'yes'})
    assert response.status_code == 302
    python_job.refresh_from_db(); assert python_job.group == group
    body = admin_client.get(reverse('python_jobs:add')).content.decode()
    assert 'panel/schedule.js' in body and 'panel/python-jobs.js' in body
    assert 'data-searchable' in body
