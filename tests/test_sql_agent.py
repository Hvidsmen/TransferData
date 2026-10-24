from copy import deepcopy
from datetime import datetime
from unittest.mock import MagicMock, patch
from contextlib import contextmanager
from uuid import UUID
import pytest
from django.urls import reverse
from django.core.cache import cache
from transferdata.erp.control.models import DatabaseConnection
from transferdata.sql_agent.services import schedule_label, stamp, duration, decorate, read, JOBS, HISTORY, STEPS

JOB_ID = '01234567-89ab-cdef-0123-456789abcdef'


def schedule(**kwargs):
    return dict(freq_type=4, freq_interval=1, freq_subday_type=1, freq_subday_interval=0,
                freq_relative_interval=0, freq_recurrence_factor=1, active_start_time=80000,
                active_end_time=235959, **kwargs)


def test_agent_dates_and_long_duration_do_not_use_datetime_hour_limits():
    assert stamp(20261024, 70503) == datetime(2026, 10, 24, 7, 5, 3)
    assert stamp(0, 0) is None
    assert stamp(20260231, 0) is None
    assert duration(1253004) == '125:30:04'
    assert decorate({'run_status': 0, 'run_date': 20261024, 'run_time': 0, 'run_duration': 30})['status'] == 'failed'
    assert decorate({'active': 1, 'last_status': 1})['label'] == 'Есть незавершённая активность'


def test_weekly_monthly_and_intraday_schedule_descriptions():
    data = schedule()
    assert schedule_label(data) == 'Ежедневно · 08:00'
    data.update(freq_type=8, freq_interval=2|16, freq_recurrence_factor=2)
    assert schedule_label(data) == 'Каждые 2 нед. · Пн, Чт · 08:00'
    data.update(freq_type=16, freq_interval=25, freq_recurrence_factor=1)
    assert '25-го числа' in schedule_label(data)
    data.update(freq_type=32, freq_interval=9, freq_relative_interval=16)
    assert 'Последний рабочий день' in schedule_label(data)
    data.update(freq_type=4, freq_interval=1, freq_subday_type=4, freq_subday_interval=15)
    assert 'каждые 15 мин' in schedule_label(data)
    data.update(freq_type=64)
    assert schedule_label(data) == 'При запуске SQL Server Agent'


@pytest.fixture
def agent_connection(db):
    return DatabaseConnection.objects.create(name='DWH', server='vm-dwh', database='DataWH', role='TARGET', authentication_type='WINDOWS')


@pytest.fixture
def snapshot():
    return {'jobs': [dict(id=JOB_ID, name='SQL nightly', enabled=True, category='Maintenance', active=False,
        last_status=1, status='success', label='Успешно', last_run=datetime(2026,10,24,2), duration='0:05:00', next_run=None, schedules=[])],
        'observed_at': datetime(2026,10,24,3), 'job': dict(id=JOB_ID, name='SQL nightly', enabled=True), 'steps': [], 'history': []}


@pytest.mark.django_db
def test_only_admin_can_read_and_post_cannot_change_agent(client, django_user_model, agent_connection, snapshot):
    url = reverse('sql_agent_jobs')
    with patch('transferdata.sql_agent.views.read', return_value=snapshot) as fetch:
        assert client.get(url).status_code == 302
        viewer = django_user_model.objects.create_user('agent-viewer', is_staff=True)
        client.force_login(viewer)
        assert client.get(url).status_code == 403
        fetch.assert_not_called()
        admin = django_user_model.objects.create_superuser('agent-admin')
        client.force_login(admin)
        response = client.get(url)
        assert response.status_code == 200 and b'SQL nightly' in response.content
        assert response.context['connection'] == agent_connection
        assert client.post(url, {'operation':'delete'}).status_code == 405
        detail = client.get(reverse('sql_agent_detail', args=[JOB_ID]), {'connection':agent_connection.pk})
        assert detail.status_code == 200
        assert 'Шаги задания' in detail.content.decode()


@pytest.mark.django_db
def test_ambiguous_connection_requires_choice_and_driver_errors_are_not_exposed(client, django_user_model, agent_connection):
    client.force_login(django_user_model.objects.create_superuser('agent-error-admin'))
    other = DatabaseConnection.objects.create(name='DWH copy', server='vm-dwh', database='Other', role='TARGET', authentication_type='WINDOWS')
    with patch('transferdata.sql_agent.views.read', side_effect=RuntimeError('password=DO-NOT-SHOW')) as fetch:
        response = client.get(reverse('sql_agent_jobs'))
        fetch.assert_not_called()
        assert response.context['connection'] is None
        response = client.get(reverse('sql_agent_jobs'), {'connection':other.pk})
        assert response.context['agent_error']
        assert 'msdb' in response.content.decode() and b'DO-NOT-SHOW' not in response.content


@pytest.mark.django_db
def test_msdb_monitor_preserves_saved_connection_and_uses_cache_and_bound_guid(agent_connection):
    cache.clear()
    seen = []
    @contextmanager
    def connection(config, **kwargs):
        seen.append(config)
        yield MagicMock()
    jobs = [dict(id=JOB_ID, name='SQL nightly', enabled=True, category='DWH', last_status=1, active=0,
                 run_date=20261024, run_time=10000, run_duration=123)]
    plan = dict(schedule(), job_id=JOB_ID, name='Daily', enabled=True)
    with patch('transferdata.sql_agent.services.connect', connection), patch('transferdata.sql_agent.services.rows',
        side_effect=[deepcopy(jobs), [plan], deepcopy(jobs), [plan], [], []]) as query:
        first = read(agent_connection)
        assert first['jobs'][0]['schedules'][0]['label'] == 'Ежедневно · 08:00'
        assert read(agent_connection) == first
        read(agent_connection, UUID(JOB_ID))
        assert query.call_args_list[-2].args[1:] == (HISTORY, (JOB_ID,))
        assert query.call_args_list[-1].args[1:] == (STEPS, (JOB_ID,))
    assert all(config.database == 'msdb' and config.connection_timeout <= 3 for config in seen)
    agent_connection.refresh_from_db()
    assert agent_connection.database == 'DataWH'
    assert 'step_id = 0' in JOBS and 'ORDER BY agent_start_date DESC' in JOBS
    assert 'command' not in STEPS.lower()


@pytest.mark.django_db
def test_uppercase_sql_server_job_guid_can_be_rendered_and_opened(client, django_user_model, agent_connection):
    cache.clear()
    client.force_login(django_user_model.objects.create_superuser('agent-guid-admin'))
    record = dict(id=JOB_ID.upper(), name='Uppercase SQL job', enabled=True, category='DWH', last_status=1,
        active=0, run_date=20261024, run_time=10000, run_duration=123)
    @contextmanager
    def connection(*args, **kwargs):
        yield MagicMock()
    with patch('transferdata.sql_agent.services.connect', connection), patch('transferdata.sql_agent.services.rows',
        side_effect=[[deepcopy(record)], [], [deepcopy(record)], [], [], []]):
        response = client.get(reverse('sql_agent_jobs'), {'connection':agent_connection.pk})
        assert response.status_code == 200
        detail_url = reverse('sql_agent_detail', args=[UUID(JOB_ID)])
        assert detail_url in response.content.decode()
        assert client.get(detail_url, {'connection':agent_connection.pk}).status_code == 200
