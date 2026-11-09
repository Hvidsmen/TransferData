from types import SimpleNamespace
from uuid import UUID
from unittest.mock import patch
import subprocess
import pytest
from django.urls import reverse
from django.test import Client
from transferdata.web.portal_update import service

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin(client, django_user_model, settings, tmp_path):
    settings.STATE_DIR = tmp_path
    client.force_login(django_user_model.objects.create_superuser('updater-admin'))
    return client


def test_only_admin_can_request_or_view_updates(client, django_user_model, settings, tmp_path):
    settings.STATE_DIR = tmp_path
    assert client.get(reverse('portal_update')).status_code == 302
    user = django_user_model.objects.create_user('update-viewer', is_staff=True)
    client.force_login(user)
    with patch.object(service, 'enqueue') as start:
        assert client.post(reverse('portal_update'), {'confirmed':'yes'}).status_code == 403
        assert client.get(reverse('portal_update_status', args=[UUID(int=1)])).status_code == 403
        start.assert_not_called()


def test_confirmation_post_and_csrf_are_required(admin):
    with patch.object(service, 'enqueue') as start:
        assert admin.get(reverse('portal_update')).status_code == 200
        admin.post(reverse('portal_update'), {})
        start.assert_not_called()
    strict = Client(enforce_csrf_checks=True)
    strict.cookies = admin.cookies
    assert strict.post(reverse('portal_update'), {'confirmed':'yes'}).status_code == 403


def test_enqueue_is_durable_and_duplicate_request_is_blocked(admin):
    user = SimpleNamespace(pk=1, username='admin')
    with patch.object(service, 'configured', return_value=True), patch.object(service.subprocess, 'run') as launch:
        run = service.enqueue(user)
        with pytest.raises(service.UpdateError, match='уже принят'):
            service.enqueue(user)
    assert launch.call_count == 1
    assert launch.call_args.args[0][-1] == service.START
    assert service.status(run)['state'] == 'queued'
    assert service.latest()['id'] == run
    # Hex file IDs must be converted to UUID objects before reversing Django URLs.
    page = admin.get(reverse('portal_update'), {'run':run})
    assert page.status_code == 200
    assert reverse('portal_update_status', args=[UUID(run)]) in page.content.decode()
    response = admin.get(reverse('portal_update_status', args=[UUID(run)]))
    assert response.json()['id'] == run and 'no-store' in response['Cache-Control']


def test_failed_launch_is_sanitized_and_does_not_leave_a_lock(admin):
    with patch.object(service, 'configured', return_value=True), patch.object(service.subprocess, 'run', side_effect=subprocess.CalledProcessError(1,'powershell',stderr='password=hidden')):
        with pytest.raises(service.UpdateError) as exc:
            service.enqueue(SimpleNamespace(pk=1,username='admin'))
    assert 'hidden' not in str(exc.value)
    assert not (service.root() / 'request.lock').exists()
    assert service.latest()['state'] == 'failed'


def test_ambiguous_launch_timeout_keeps_lock_and_prevents_a_second_update(admin):
    with patch.object(service, 'configured', return_value=True), patch.object(service.subprocess, 'run', side_effect=subprocess.TimeoutExpired('powershell',20)):
        with pytest.raises(service.UpdateError):
            service.enqueue(SimpleNamespace(pk=1,username='admin'))
    assert (service.root() / 'request.lock').exists()
    assert service.latest()['phase'] == 'launch_uncertain'


def test_maintenance_blocks_writes_but_keeps_update_status_readable(admin, settings):
    (settings.STATE_DIR / 'maintenance.flag').touch()
    assert admin.post(reverse('portal_update'), {'confirmed':'yes'}).status_code == 503
    assert admin.get(reverse('portal_update')).status_code == 200


def test_status_accepts_windows_utf8_bom_and_rejects_wrong_identity(admin):
    from uuid import uuid4
    run = uuid4().hex
    path = service.root() / 'runs' / (run + '.json')
    path.parent.mkdir(parents=True)
    import json
    path.write_text(json.dumps({'version':1,'id':run,'state':'success','events':[]}), encoding='utf-8-sig')
    assert service.status(run)['state'] == 'success'
    path.write_text(json.dumps({'version':1,'id':uuid4().hex,'state':'success'}), encoding='utf-8-sig')
    assert service.status(run) is None


def test_acknowledged_updater_is_not_marked_failed_after_launcher_timeout(admin):
    def launched(*args, **kwargs):
        state = service.latest()
        state.update(state='running',phase='wait',step=3)
        service.atomic_json(service.root()/'runs'/(state['id']+'.json'),state)
        raise subprocess.TimeoutExpired('powershell',20)
    with patch.object(service, 'configured', return_value=True), patch.object(service.subprocess, 'run', side_effect=launched):
        run = service.enqueue(SimpleNamespace(pk=1,username='admin'))
    assert service.status(run)['state'] == 'running'
    assert (service.root()/'request.lock').exists()


def test_maintenance_routes_admin_to_update_without_losing_session(admin, settings):
    (settings.STATE_DIR / 'maintenance.flag').touch()
    session_user = admin.session['_auth_user_id']
    response = admin.get('/python/jobs/')
    assert response.status_code == 302 and response.url == reverse('portal_update')
    page = admin.get(response.url)
    assert page.status_code == 200 and 'Обновление портала' in page.content.decode()
    assert admin.session['_auth_user_id'] == session_user
    live = admin.get('/python/jobs/', HTTP_X_ETL_LIVE='1')
    assert live.status_code == 503 and live.headers['X-ETL-Update-URL'] == reverse('portal_update')
    (settings.STATE_DIR / 'maintenance.flag').unlink()
    assert admin.get('/python/jobs/').status_code == 200


def test_update_navigation_preserves_login_return_and_non_admin_permissions(client, django_user_model, settings, tmp_path):
    settings.STATE_DIR = tmp_path
    (tmp_path / 'maintenance.flag').touch()
    response = client.get(reverse('portal_update'))
    assert response.status_code == 302 and 'next=/administration/update/' in response.url
    viewer = django_user_model.objects.create_user('maintenance-viewer', is_staff=True)
    client.force_login(viewer)
    assert client.get(reverse('portal_update')).status_code == 403
    assert client.get('/python/jobs/').status_code == 403


def test_existing_authenticated_session_is_adopted_without_logging_out(admin, settings):
    name = settings.SESSION_COOKIE_NAME
    assert name != 'sessionid'
    key = admin.cookies[name].value
    del admin.cookies[name]
    admin.cookies['sessionid'] = key
    response = admin.get(reverse('portal_update'))
    assert response.status_code == 200
    assert response.cookies[name].value == key
    assert admin.session['_auth_user_id']
    # The unrelated application's legacy cookie is not overwritten or deleted.
    assert 'sessionid' not in response.cookies


def test_unrelated_legacy_session_cannot_authenticate_and_has_no_effect_on_portal_cookie(admin, settings):
    name = settings.SESSION_COOKIE_NAME
    client = Client()
    client.cookies['sessionid'] = 'x' * 32
    response = client.get(reverse('portal_update'))
    assert response.status_code == 302 and '/login/' in response.url
    assert name not in response.cookies or (response.cookies[name].value == '' and str(response.cookies[name]['max-age']) == '0')
    admin.cookies['sessionid'] = 'x' * 32
    assert admin.get(reverse('portal_update')).status_code == 200
