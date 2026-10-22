import copy
import json
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from transferdata.erp.control.forms import JobForm
from transferdata.erp.control.models import OneCObject, OneCScan
from transferdata.erp.control.onec import (
    connection_fingerprint, enqueue_scan, mapping_from_metadata, read_metadata,
    scan_next, source_query,
)
from transferdata.erp.engine.exceptions import ETLError
from transferdata.erp.engine.extractor import source_sql

pytestmark = pytest.mark.django_db


@pytest.fixture
def definition():
    return {'protocol': 1, 'ok': True, 'name': 'Справочник.БизнесРегионы', 'table': '_Reference123',
            'configuration': 'ERP', 'version': '2.5', 'year_offset': 2000,
            'properties': [
                {'name': 'Ссылка', 'columns': [{'name': '_IDRRef', 'purpose': 'Value', 'type': 'binary(16)'}]},
                {'name': 'ВерсияДанных', 'columns': [{'name': '_Version', 'purpose': 'Value', 'type': 'binary(8)', 'row_version': True}]},
                {'name': 'Наименование', 'columns': [{'name': '_Description', 'purpose': 'Value', 'type': 'nvarchar(100)'}]},
                {'name': 'Регион', 'columns': [
                    {'name': '_Fld44_TYPE', 'purpose': 'Tag', 'type': 'binary(1)'},
                    {'name': '_Fld44_RTRef', 'purpose': 'TypeCode', 'type': 'binary(4)'},
                    {'name': '_Fld44_RRRef', 'purpose': 'Identity', 'type': 'binary(16)'}]},
            ], 'table_parts': []}


@pytest.fixture
def obj(job, definition):
    return OneCObject.objects.create(connection=job.source_connection, kind='Справочник', name=definition['name'],
        metadata=definition, fingerprint=connection_fingerprint(job.source_connection))


def test_mapping_is_from_metadata_not_guessed_and_values_stay_raw(definition):
    mapping = mapping_from_metadata(definition)
    query = source_query(mapping)
    assert 'FROM [dbo].[_Reference123]' in query
    assert '[_Description] AS [Наименование]' in query
    assert '[_Fld44_RTRef] AS [Регион__TypeCode]' in query
    assert '[_IDRRef] AS [Ссылка]' in query
    assert 'CONVERT(binary(8), [_Version]) AS [ВерсияДанных]' in query
    assert 'DATEADD' not in query and 'JOIN' not in query
    assert len(mapping['columns']) == 6
    assert mapping['year_offset'] == 2000


def test_date_correction_uses_exact_requested_threshold_and_metadata_type(definition):
    definition['properties'].append({'name': 'Дата', 'columns': [
        {'name': '_Date_Time', 'purpose': 'Value', 'type': 'datetime2(0)', 'is_date': True}]})
    definition['properties'].append({'name': 'ТекстоваяДата', 'columns': [
        {'name': '_FldDateText', 'purpose': 'Value', 'type': 'nvarchar(50)'}]})
    mapping = mapping_from_metadata(definition)
    query = source_query(mapping, adjust_dates=True)
    assert 'CASE WHEN YEAR([_Date_Time]) < 3753 THEN [_Date_Time] ELSE DATEADD(year, -2000, [_Date_Time]) END AS [Дата]' in query
    assert '[_FldDateText] AS [ТекстоваяДата]' in query
    assert query.count('DATEADD') == 1
    assert 'DATEADD' not in source_query(mapping, adjust_dates=False)


def test_mapping_handles_long_names_and_case_collisions(definition):
    definition['properties'] = [
        {'name': 'Имя' * 80, 'columns': [{'name': '_Fld1'}]},
        {'name': 'Имя' * 80, 'columns': [{'name': '_Fld2'}]},
        {'name': 'имя' * 80, 'columns': [{'name': '_Fld3'}]},
    ]
    columns = mapping_from_metadata(definition)['columns']
    assert len({c['alias'].casefold() for c in columns}) == 3
    assert all(len(c['alias']) <= 128 for c in columns)


@pytest.mark.parametrize('field', ['table', 'column'])
def test_mapping_rejects_sql_injection_in_physical_identifiers(definition, field):
    if field == 'table':
        definition['table'] = 'x]; DROP TABLE target;--'
    else:
        definition['properties'][0]['columns'][0]['name'] = 'x]; DROP TABLE target;--'
    with pytest.raises(ValidationError):
        mapping_from_metadata(definition)


def test_catalogue_import_is_atomic_and_retires_missing_objects(job, obj):
    obsolete = OneCObject.objects.create(connection=job.source_connection, name='Справочник.Старый', kind='Справочник')
    scan, created = enqueue_scan(job.source_connection)
    assert created
    assert enqueue_scan(job.source_connection)[1] is False
    data = {'objects': [{'name': obj.name, 'kind': 'Справочник'}], 'configuration': 'ERP'}
    with patch('transferdata.erp.control.onec.read_metadata', return_value=data):
        assert scan_next()
    obj.refresh_from_db()
    obsolete.refresh_from_db()
    scan.refresh_from_db()
    assert scan.status == 'SUCCESS'
    assert obj.metadata and obj.is_active
    assert not obsolete.is_active
    scan, _ = enqueue_scan(job.source_connection)
    with patch('transferdata.erp.control.onec.read_metadata', return_value={'objects': data['objects'] * 2}):
        scan_next()
    scan.refresh_from_db()
    assert scan.status == 'FAILED'
    assert OneCObject.objects.get(pk=obj.pk).is_active


def test_object_refresh_saves_only_verified_matching_structure(job, obj, definition):
    changed = copy.deepcopy(definition)
    changed['table'] = '_Reference124'
    scan, _ = enqueue_scan(job.source_connection, obj)
    with patch('transferdata.erp.control.onec.read_metadata', return_value=changed):
        scan_next()
    obj.refresh_from_db()
    assert obj.metadata['table'] == '_Reference124'
    changed['name'] = 'Справочник.Другой'
    scan, _ = enqueue_scan(job.source_connection, obj)
    with patch('transferdata.erp.control.onec.read_metadata', return_value=changed):
        scan_next()
    scan.refresh_from_db()
    obj.refresh_from_db()
    assert scan.status == 'FAILED' and obj.metadata['table'] == '_Reference124'


def test_connection_change_during_scan_preserves_previous_catalogue(job, obj):
    scan, _ = enqueue_scan(job.source_connection)
    def read(*args):
        type(job.source_connection).objects.filter(pk=job.source_connection_id).update(database='changed')
        return {'objects': [{'name': obj.name, 'kind': 'Справочник'}]}
    with patch('transferdata.erp.control.onec.read_metadata', side_effect=read):
        scan_next()
    scan.refresh_from_db()
    assert scan.status == 'FAILED'
    assert OneCObject.objects.get(pk=obj.pk).metadata


def test_scan_error_never_persists_driver_text(job):
    scan, _ = enqueue_scan(job.source_connection)
    with patch('transferdata.erp.control.onec.read_metadata', side_effect=RuntimeError('Password=secret')):
        scan_next()
    scan.refresh_from_db()
    assert scan.status == 'FAILED' and 'secret' not in scan.message


def test_job_form_freezes_mapping_and_detects_metadata_changes(job, obj):
    data = {'name': 'Регионы из 1С', 'source_connection': job.source_connection_id,
            'target_connection': job.target_connection_id, 'source_type': 'ONEC', 'one_c_object': obj.pk,
            'target_schema': 'dbo', 'target_table': 'BusinessRegions', 'load_mode': 'FULL',
            'batch_size': 1000, 'command_timeout': 300, 'is_active': 'on'}
    form = JobForm(data)
    assert form.is_valid(), form.errors
    saved = form.save()
    saved.runnable()
    assert '[_Description] AS [Наименование]' in source_sql(saved)
    obj.metadata = {**obj.metadata, 'table': '_Reference999'}
    obj.save()
    saved.refresh_from_db()
    with pytest.raises(ValidationError):
        saved.runnable()
    form = JobForm(data, instance=saved)
    assert form.is_valid(), form.errors
    assert form.save().one_c_mapping['table'] == '_Reference999'


def test_object_cannot_be_used_from_another_source(job, obj):
    from transferdata.erp.control.onec import validate_object
    with pytest.raises(ValidationError):
        validate_object(obj, job.target_connection)


def test_views_are_readonly_until_authorized_post(client, administrator_user, observer, job, obj):
    client.force_login(observer)
    url = reverse('erp:onec_refresh', args=[job.source_connection_id])
    assert client.post(url, {}).status_code == 403
    for path in [reverse('erp:onec_catalogue'), reverse('erp:onec_detail', args=[obj.pk]), reverse('erp:onec_options')]:
        assert client.get(path).status_code == 200
    assert OneCScan.objects.count() == 0
    client.force_login(administrator_user)
    assert client.get(url).status_code == 405
    assert client.post(url, {'object': obj.pk}).status_code == 302
    assert OneCScan.objects.get().object_id == obj.pk
    response = client.get(reverse('erp:job_create'), {'onec': obj.pk})
    assert response.context['form']['source_type'].value() == 'ONEC'
    assert response.context['form']['source_connection'].value() == job.source_connection_id
    options = client.get(reverse('erp:onec_options'), {'connection': job.source_connection_id}).json()
    assert options == {'objects': [{'id': obj.pk, 'name': obj.name}]}


def test_reader_uses_private_stdin_and_never_secret_arguments(job, settings, tmp_path):
    settings.STATE_DIR = tmp_path
    for name in ['dotnet', 'reader.dll']:
        (tmp_path / name).write_text('fake')
    manifest = {'protocol': 1, 'dotnet': str(tmp_path / 'dotnet'), 'assembly': str(tmp_path / 'reader.dll')}
    (tmp_path / 'onec-helper.json').write_text(json.dumps(manifest))
    connection = job.source_connection
    connection.authentication_type, connection.username, connection.credential_reference = 'SQL', 'reader', 'ONEC_TEST_PASSWORD'
    def run(command, **kwargs):
        assert 'never-on-command-line' not in str(command)
        assert json.loads(kwargs['input'])['password'] == 'never-on-command-line'
        assert kwargs['shell'] is False
        kwargs['stdout'].write(b'{"protocol":1,"ok":true,"objects":[]}')
        return SimpleNamespace(returncode=0)
    with patch.dict('os.environ', {'ONEC_TEST_PASSWORD': 'never-on-command-line'}), patch('subprocess.run', side_effect=run):
        assert read_metadata(connection)['ok']
    connection.authentication_type = 'WINDOWS'
    with patch('subprocess.run', side_effect=subprocess.TimeoutExpired(['dotnet'], 600)):
        with pytest.raises(ETLError):
            read_metadata(job.source_connection)


def test_missing_helper_is_actionable(job, settings, tmp_path):
    settings.STATE_DIR = tmp_path
    with pytest.raises(ETLError, match='Install-1CMetadata'):
        read_metadata(job.source_connection)


def test_erp_worker_processes_metadata_queue_without_creating_data_load(job):
    from django.core.management import call_command
    from transferdata.erp.control.models import ETLExecution
    scan, _ = enqueue_scan(job.source_connection)
    data = {'objects': [{'name': 'Справочник.БизнесРегионы', 'kind': 'Справочник'}]}
    with patch('transferdata.erp.control.onec.read_metadata', return_value=data):
        call_command('etl_worker', once=True)
    scan.refresh_from_db()
    assert scan.status == 'SUCCESS'
    assert ETLExecution.objects.count() == 0


def test_worker_recovers_interrupted_scan_without_changing_cached_structure(job, obj):
    from django.core.management import call_command
    scan = OneCScan.objects.create(connection=job.source_connection, object=obj, status='RUNNING')
    with patch('transferdata.erp.control.onec.read_metadata') as reader:
        call_command('etl_worker', once=True)
        reader.assert_not_called()
    scan.refresh_from_db()
    obj.refresh_from_db()
    assert scan.status == 'FAILED' and obj.metadata


def test_metadata_queue_respects_maintenance_pause(job):
    with patch('transferdata.erp.control.onec.is_paused', return_value=True):
        with pytest.raises(ValidationError):
            enqueue_scan(job.source_connection)
        assert scan_next() is False


def test_refresh_requires_csrf(administrator_user, job):
    from django.test import Client
    client = Client(enforce_csrf_checks=True)
    client.force_login(administrator_user)
    assert client.post(reverse('erp:onec_refresh', args=[job.source_connection_id])).status_code == 403


def test_scan_progress_reports_missing_worker_and_real_phases(client, administrator_user, job, definition):
    from django.utils import timezone
    from transferdata.erp.control.models import WorkerLease
    client.force_login(administrator_user)
    scan, _ = enqueue_scan(job.source_connection)
    url = reverse('erp:onec_catalogue') + f'?connection={job.source_connection_id}'
    response = client.get(url)
    assert 'Нет свежего сигнала ERP-worker' in response.content.decode()
    assert 'data-scan-created' in response.content.decode()
    WorkerLease.objects.update_or_create(pk=1, defaults={'owner': 'test', 'heartbeat': timezone.now()})
    assert 'Ожидаем свободный ERP-worker' in client.get(url).content.decode()

    def metadata(*args):
        scan.refresh_from_db()
        assert scan.status == 'RUNNING' and scan.phase == 'READING' and scan.started_at
        content = client.get(url).content.decode()
        assert 'Чтение метаданных из базы 1С' in content
        assert 'Процент готовности источник не сообщает' in content
        return {'objects': [{'name': definition['name'], 'kind': 'Справочник'}]}

    with patch('transferdata.erp.control.onec.read_metadata', side_effect=metadata):
        assert scan_next()
    scan.refresh_from_db()
    assert scan.status == 'SUCCESS' and scan.phase == 'SAVING' and scan.finished_at >= scan.started_at
    assert 'Готово' in client.get(url).content.decode()
