import json
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.urls import reverse
from transferdata.web.deployment import load


def test_default_installation_and_strict_yds_manifest(tmp_path):
    assert load(tmp_path)['name'] == 'ETL Dev'
    (tmp_path / 'deployment.json').write_text(json.dumps({'version': 1, 'profile': 'yds'}), encoding='utf-8-sig')
    config = load(tmp_path)
    assert config['name'] == 'etl-dev-yds'
    assert config['workers'] == ('onec', 'python')
    assert config['host'] == '192.168.70.9'
    (tmp_path / 'deployment.json').write_text('{"version":1,"profile":"unknown"}')
    with pytest.raises(ImproperlyConfigured):
        load(tmp_path)


def test_yds_settings_load_in_fresh_process_without_old_company_hosts(tmp_path):
    (tmp_path / 'deployment.json').write_text('{"version":1,"profile":"yds"}')
    env = os.environ.copy()
    env['TRANSFERDATA_STATE_DIR'] = str(tmp_path)
    env.pop('TRANSFERDATA_ALLOWED_HOSTS', None)
    env.pop('TRANSFERDATA_CSRF_ORIGINS', None)
    result = subprocess.run([sys.executable, '-c', "import json; from transferdata.web import settings as s; print(json.dumps([s.PORTAL_NAME,s.ALLOWED_HOSTS,s.CSRF_TRUSTED_ORIGINS,s.PORTAL_WORKERS,s.SESSION_COOKIE_NAME,s.CSRF_COOKIE_NAME]))"], env=env, capture_output=True, text=True, check=True)
    name, hosts, origins, workers, session_cookie, csrf_cookie = json.loads(result.stdout)
    assert name == 'etl-dev-yds'
    assert '192.168.70.9' in hosts
    assert not any('daichi' in h for h in hosts + origins)
    assert workers == ['onec', 'python']
    assert session_cookie == 'etl_dev_yds_sessionid' and csrf_cookie == 'etl_dev_yds_csrftoken'
    assert (tmp_path / 'django-secret.key').exists()


@pytest.fixture
def yds(settings):
    settings.PORTAL_NAME = 'etl-dev-yds'
    settings.PORTAL_TASK_PREFIX = 'etl-dev-yds'
    settings.PORTAL_WORKERS = ('onec', 'python')
    settings.DEPLOYMENT = {'profile': 'yds'}


@pytest.mark.django_db
def test_yds_portal_hides_sql_and_agent_and_does_not_query_them(client, django_user_model, yds):
    client.force_login(django_user_model.objects.create_superuser('yds-admin'))
    with patch('transferdata.sql_agent.overview.cards') as agent, patch('transferdata.web.panel.portal_monitoring.transferdata_jobs') as sql:
        response = client.get('/')
    assert response.status_code == 200
    html = response.content.decode()
    assert 'etl-dev-yds' in html and '1С Перенос данных' in html and 'Python обработчик' in html
    assert 'SQL Перенос данных' not in html and 'SQL Server Agent' not in html
    assert 'ETL Dev — ERP' not in html
    assert [w['key'] for w in response.context['health']] == ['onec', 'python']
    assert [w[0] for w in response.context['workers']] == ['onec', 'python']
    agent.assert_not_called()
    sql.assert_not_called()
    for path in ('/jobs/', '/connections/', '/runs/', '/transferdata/', '/sql-agent/', '/panel/job/'):
        assert client.get(path).status_code == 404
    assert client.get('/erp/').status_code == 200
    assert client.get('/python/').status_code == 200


@pytest.mark.django_db
def test_yds_python_form_has_no_company_specific_handler(yds):
    from transferdata.python_jobs.forms import JobForm
    from transferdata.python_jobs.models import Job
    form = JobForm()
    assert form.initial['handler'] == 'script'
    assert list(form.fields['handler'].choices) == [('script', 'Python-файл на сервере'), ('sql_pipeline', 'Цепочка SQL-процедур')]
    with pytest.raises(ValidationError, match='собственный Python-файл'):
        Job(name='not portable', handler='commercial_headers').clean()


@pytest.mark.django_db
def test_yds_browser_update_launches_only_yds_task(yds, settings, tmp_path):
    from transferdata.web.portal_update import service
    settings.STATE_DIR = tmp_path
    with patch.object(service, 'configured', return_value=True), patch.object(service.subprocess, 'run') as launch:
        service.enqueue(SimpleNamespace(pk=1, username='yds-admin'))
    command = launch.call_args.args[0][-1]
    assert "'etl-dev-yds '" in command
    assert "'ETL Dev '" not in command


@pytest.mark.django_db
def test_yds_admin_and_update_page_do_not_show_old_company_setup(client, django_user_model, yds):
    from transferdata.web.panel.admin import site
    user = django_user_model.objects.create_superuser('yds-admin-ui')
    client.force_login(user)
    from django.test import RequestFactory
    request = RequestFactory().get('/')
    request.user = user
    assert all(m['object_name'] not in ('Job', 'Run', 'Connection') for a in site.get_app_list(request) if a['app_label'] == 'panel' for m in a['models'])
    response = client.get(reverse('portal_update'))
    assert response.status_code == 200
    assert 'C:\\etl-dev-yds' in response.content.decode()
    assert 'C:\\Users\\ETLadmin' not in response.content.decode()


def test_fresh_yds_migration_creates_both_procedure_jobs_once(tmp_path):
    (tmp_path / 'deployment.json').write_text('{"version":1,"profile":"yds"}')
    env = os.environ.copy()
    env['TRANSFERDATA_STATE_DIR'] = str(tmp_path)
    env['DJANGO_SETTINGS_MODULE'] = 'transferdata.web.settings'
    for _ in range(2):
        subprocess.run([sys.executable, '-m', 'transferdata.web', 'migrate', '--noinput'], env=env,
                       capture_output=True, text=True, check=True)
    code = "import django,json;django.setup();from transferdata.python_jobs.models import Job,Run;print(json.dumps([[(j.name,len(j.steps),j.enabled,j.transaction_mode) for j in Job.objects.order_by('pk')],Run.objects.count()]))"
    result = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, text=True, check=True)
    jobs, runs = json.loads(result.stdout)
    assert len(jobs) == 2 and [j[1] for j in jobs] == [3, 7]
    assert all(not j[2] and j[3] == 'atomic' for j in jobs)
    assert runs == 0
