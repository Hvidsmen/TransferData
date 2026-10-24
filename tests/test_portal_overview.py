import pytest
from django.urls import reverse
from django.contrib.auth import get_user_model
from transferdata.web.panel.models import Connection, Job, JobGroup, Run
from transferdata.erp.control.models import DatabaseConnection, ETLJob, ETLExecution
from transferdata.python_jobs.models import Job as PythonJob, Run as PythonRun

pytestmark = pytest.mark.django_db


@pytest.fixture
def portal_jobs(client, monkeypatch):
    monkeypatch.setattr('transferdata.sql_agent.overview.schedule_refresh', lambda *args: None)
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


@pytest.fixture
def agent_on_home(portal_jobs):
    from datetime import datetime
    from django.core.cache import cache
    from django.utils import timezone
    from transferdata.sql_agent.services import key
    profile = DatabaseConnection.objects.get(name='Source')
    data = {'observed_at':timezone.now(), 'jobs':[
        {'id':'01234567-89ab-cdef-0123-456789abcdef', 'name':'Agent finance', 'category':'Finance', 'enabled':True,
         'active':False, 'last_status':0, 'status':'failed', 'label':'Ошибка', 'last_run':datetime(2026,10,25,8),
         'last_success':datetime(2026,10,24,8), 'next_run':datetime(2026,10,26,8), 'schedules':[{'enabled':True}]},
        {'id':'11234567-89ab-cdef-0123-456789abcdef', 'name':'Agent disabled', 'category':'Finance', 'enabled':False,
         'active':False, 'last_status':1, 'status':'success', 'label':'Успешно', 'last_run':datetime(2026,10,25,8),
         'next_run':datetime(2026,10,26,8), 'schedules':[{'enabled':True}]}
    ]}
    cache.set(key(profile), data, 15)
    return portal_jobs, profile, data


def test_home_includes_agent_categories_history_and_attention_without_delete(agent_on_home):
    (client, _), profile, data = agent_on_home
    response = client.get(reverse('etl_portal'))
    assert response.context['job_total'] == 6
    agent = next(group for group in response.context['portal_groups'] if group['name'] == 'SQL Agent · Finance')
    assert agent['workers'][0]['key'] == 'agent' and agent['count'] == 2
    items = agent['workers'][0]['jobs']
    assert items[0]['last_success'] == data['jobs'][0]['last_success']
    assert items[1]['next_run'] is None and items[1]['disabled']
    assert any(item['name'] == 'Agent finance' for item in response.context['attention_jobs'])
    assert items[0]['url'].endswith(f'?connection={profile.pk}')
    filtered = client.get(reverse('etl_portal'), {'worker':'agent','status':'failed','q':'FINANCE'})
    assert filtered.context['job_shown'] == 1
    body = filtered.content.decode()
    assert 'Agent finance' in body and 'время SQL Server' in body
    assert 'name="operation" value="delete"' not in body


def test_agent_failure_keeps_main_usable_and_stale_snapshot_explicit(agent_on_home):
    from datetime import timedelta
    from django.core.cache import cache
    from django.utils import timezone
    from transferdata.sql_agent.services import key
    (client, _), profile, data = agent_on_home
    cache.delete(key(profile))
    data['observed_at'] = timezone.now() - timedelta(minutes=10)
    cache.set(key(profile) + ':overview', data, 60)
    cache.set(key(profile) + ':overview-error', True, 30)
    response = client.get(reverse('etl_portal'))
    assert response.status_code == 200 and response.context['job_total'] == 6
    assert response.context['agent_notice']['state'] == 'error'
    assert 'предыдущий снимок' in response.content.decode()
    cache.delete(key(profile) + ':overview')
    response = client.get(reverse('etl_portal'))
    assert response.status_code == 200 and response.context['job_total'] == 4
    assert 'msdb' in response.content.decode()


def test_agent_cards_are_not_exposed_to_non_admin(agent_on_home):
    from transferdata.sql_agent.overview import cards
    from types import SimpleNamespace
    (_, _), _, _ = agent_on_home
    assert cards(SimpleNamespace(user=SimpleNamespace(is_superuser=False))) == ([], None)


def test_agent_overview_refresh_failure_is_safe_and_keeps_last_good_data(monkeypatch):
    from django.core.cache import cache
    from transferdata.sql_agent import overview
    monkeypatch.setattr(overview, 'close_old_connections', lambda:None)
    def failed_read(config):
        raise RuntimeError('password=never-display')
    monkeypatch.setattr(overview.services, 'read', failed_read)
    cache.set('agent-test-snapshot', {'jobs':[]}, 60)
    overview._refresh(None, 'agent-test-snapshot', 'agent-test-error')
    assert cache.get('agent-test-error') is True
    assert cache.get('agent-test-snapshot') == {'jobs':[]}


def test_agent_connection_choice_is_shared_with_home():
    from types import SimpleNamespace
    from transferdata.sql_agent.overview import choose_connection
    first = SimpleNamespace(pk=1, server='vm-dwh', role='TARGET')
    second = SimpleNamespace(pk=2, server='vm-dwh', role='TARGET')
    assert choose_connection([first, second]) is None
    assert choose_connection([first, second], saved=2) is second


def test_agent_requests_only_schedule_a_bounded_background_refresh(monkeypatch):
    from unittest.mock import MagicMock
    from transferdata.sql_agent import overview
    thread = MagicMock()
    monkeypatch.setattr(overview, 'Thread', thread)
    monkeypatch.setattr(overview, '_refreshing', False)
    overview.schedule_refresh(None, 'snapshot', 'error')
    overview.schedule_refresh(None, 'snapshot', 'error')
    assert thread.call_count == 1 and thread.return_value.start.call_count == 1
    assert thread.call_args.kwargs['daemon'] is True


def test_unexpected_agent_overview_error_cannot_break_local_jobs(portal_jobs, monkeypatch):
    client, _ = portal_jobs
    def broken(request):
        raise RuntimeError('password=never-display')
    monkeypatch.setattr('transferdata.sql_agent.overview.cards', broken)
    response = client.get(reverse('etl_portal'))
    assert response.status_code == 200 and response.context['job_total'] == 4
    assert response.context['agent_notice']['state'] == 'error'
    assert b'never-display' not in response.content
