import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from transferdata.web.panel.models import Connection, Job, Run
from transferdata.erp.control.models import DatabaseConnection, ETLJob, ETLExecution
from transferdata.python_jobs.models import Job as PythonJob, Run as PythonRun

pytestmark = pytest.mark.django_db


@pytest.fixture
def histories(client):
    user = get_user_model().objects.create_superuser('retry-admin', password='test-password')
    client.force_login(user)
    td = Job.objects.create(name='SQL', source_table='ModelCode', target_table='ModelCode', connection=Connection.objects.create(name='SQL', target_host='postgres', target_database='analytics'))
    tr = Run.objects.create(job=td, action='run', status='failed', snapshot={})
    source = DatabaseConnection.objects.create(name='DWH source', server='vm-dwh', database='DataWH', authentication_type='WINDOWS', role='SOURCE')
    target = DatabaseConnection.objects.create(name='DWH target', server='other', database='warehouse', authentication_type='WINDOWS', role='TARGET')
    erp = ETLJob.objects.create(name='1C', source_connection=source, target_connection=target, source_table='dbo.products', target_table='products')
    er = ETLExecution.objects.create(job=erp, status='FAILED')
    python = PythonJob.objects.create(name='Python', connection=source)
    pr = PythonRun.objects.create(job=python, status='failed', snapshot={})
    return client, [(td, tr, Run, 'td_run', 'td_action'), (erp, er, ETLExecution, 'erp:execution_detail', 'erp:job_run'),
                    (python, pr, PythonRun, 'python_jobs:run', 'python_jobs:execute')]


def endpoint(job, name):
    return reverse(name, args=[job.pk, job.execution_action] if name == 'td_action' else [job.pk])


def test_history_can_start_job_and_open_new_run_without_duplicates(histories):
    client, cases = histories
    for job, old, model, detail, action in cases:
        page = client.get(reverse(detail, args=[old.pk]))
        assert page.status_code == 200 and 'Запустить повторно' in page.content.decode()
        assert endpoint(job, action) in page.content.decode()
        # Viewing a page/confirmation must never execute the job.
        client.get(endpoint(job, action))
        assert model.objects.filter(job=job).count() == 1
        response = client.post(endpoint(job, action), {'open_result': 'yes'})
        latest = model.objects.filter(job=job).exclude(pk=old.pk).get()
        assert response.url == reverse(detail, args=[latest.pk])
        page = client.get(reverse(detail, args=[old.pk]))
        assert 'Открыть активный запуск' in page.content.decode() and 'Запустить повторно' not in page.content.decode()
        duplicate = client.post(endpoint(job, action), {'open_result': 'yes'})
        assert model.objects.filter(job=job).count() == 2
        assert duplicate.url == reverse(detail, args=[latest.pk])


def test_archived_jobs_do_not_offer_retry(histories):
    client, cases = histories
    for job, old, model, detail, action in cases:
        job.archived = True; job.save()
        assert 'Запустить повторно' not in client.get(reverse(detail, args=[old.pk])).content.decode()
        client.post(endpoint(job, action), {'open_result': 'yes'})
        assert model.objects.filter(job=job).count() == 1


def test_repeat_uses_current_job_settings_and_correct_procedure_action(histories):
    client, cases = histories
    job, old, model, detail, action = cases[0]
    job.remove_nul_characters = True; job.save()
    client.post(endpoint(job, action), {'open_result': 'yes'})
    latest = model.objects.filter(job=job).exclude(pk=old.pk).get()
    assert latest.snapshot['jobs'][0]['remove_nul_characters'] is True
    latest.status = 'success'; latest.save()
    job.job_type = 'procedure'; job.procedure_name = 'sp_Test'; job.save()
    page = client.get(reverse(detail, args=[old.pk])).content.decode()
    assert reverse('td_action', args=[job.pk, 'execute']) in page
    client.post(endpoint(job, action), {'open_result': 'yes'})
    assert Run.objects.filter(job=job, status='queued').get().action == 'execute'


def test_observer_cannot_launch_from_history(histories):
    client, cases = histories
    from django.contrib.auth.models import Permission
    user = get_user_model().objects.create_user('retry-observer', is_staff=True)
    user.user_permissions.add(Permission.objects.get(content_type__app_label='panel', codename='view_run'),
                             Permission.objects.get(content_type__app_label='python_jobs', codename='view_run'))
    client.force_login(user)
    for job, old, model, detail, action in cases:
        page = client.get(reverse(detail, args=[old.pk]))
        assert page.status_code == 200 and 'Запустить повторно' not in page.content.decode()
        assert client.post(endpoint(job, action), {'open_result': 'yes'}).status_code == 403
        assert model.objects.filter(job=job).count() == 1
