import csv
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import patch, MagicMock

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command, CommandError

from transferdata.erp.reports.cash_receipts_choices import OBJECTS, DDS, BUDGET, KINDS, OPERATIONS, STRUCTURE, queries
from transferdata.erp.reports.cash_receipts import OBJECTS as ROOTS
from transferdata.erp.engine.validators import read_query

pytestmark = pytest.mark.django_db


@pytest.fixture
def metadata():
    def prop(name, *purposes):
        return {'name': name, 'columns': [{'name': '_' + name + purpose, 'purpose': purpose} for purpose in purposes or ['Value']]}
    def obj(name, props):
        return {'name': name, 'table': '_Table' + str(OBJECTS.index(name)), 'properties': props,
                'partitions': [{'key': 'shared', 'source': '_Area'}]}
    result = {}
    for name in ROOTS[:3]:
        result[name] = obj(name, [prop('Регистратор', 'Identity', 'TypeCode'), prop('Период'), prop('Активность'),
                                  prop('СтатьяДвиженияДенежныхСредств'), prop('СтатьяОборотов')])
    result[ROOTS[3]] = obj(ROOTS[3], [prop('ВидСоответствия'), prop('ЛеваяАналитика_1', 'Tag', 'TypeCode')] +
        [prop(name, 'Tag') for name in ('ЛеваяАналитика_2', 'ЛеваяАналитика_3', 'ПраваяАналитика_2', 'ПраваяАналитика_3')])
    result[ROOTS[4]] = obj(ROOTS[4], [])
    for name in ROOTS[7:9]:
        result[name] = obj(name, [prop('Ссылка'), prop('ХозяйственнаяОперация')])
    for name in [DDS, BUDGET, KINDS]:
        result[name] = obj(name, [prop('Ссылка'), prop('Код'), prop('Наименование')])
    result[OPERATIONS] = obj(OPERATIONS, [])
    result[OPERATIONS]['reference_targets'] = [
        {'name': name, 'type_code': index + 100} for index, name in enumerate(ROOTS[5:9] + [STRUCTURE])]
    result[OPERATIONS]['reference_targets'].append({'name': OPERATIONS, 'type_code': 200,
        'values': [{'name': 'ВозвратОплатыКлиенту', 'hex': '0123456789ABCDEF0123456789ABCDEF'}]})
    return result


def test_choice_queries_are_select_only_use_real_metadata_types_and_bound_dates(metadata):
    at = datetime(2026, 10, 23, 18)
    prepared = queries(metadata, at)
    assert len(prepared) == 3
    for item in prepared:
        assert read_query(item.sql)
        assert item.sql.count('?') == len(item.parameters)
        assert 'FULL OUTER JOIN' in item.sql and 'COUNT_BIG' in item.sql
        assert '_Area' in item.sql
    assert prepared[0].parameters == (at, at.replace(year=4026), at, at.replace(year=4026))
    assert '0x00000064' in prepared[0].sql and '0x00000065' in prepared[0].sql
    assert 'Активность' not in prepared[0].sql  # preserve incoming-register semantics
    assert '0x0123456789ABCDEF0123456789ABCDEF' in prepared[1].sql
    assert '0x01' in prepared[1].sql and '0x00000066' in prepared[1].sql
    assert 'ЛеваяАналитика_1TypeCode' in prepared[2].sql and '0x08' in prepared[2].sql
    assert prepared[2].sql.count('= 0x01') == 4
    assert '[Area]' not in prepared[2].sql  # quoted separator is taken from metadata, not guessed


def test_fast_mode_has_no_register_scans_and_code_is_optional(metadata):
    metadata[DDS]['properties'] = [p for p in metadata[DDS]['properties'] if p['name'] != 'Код']
    for item in queries(metadata, datetime(2026, 10, 23), counts=False):
        assert 'COUNT_BIG' not in item.sql and 'JOIN' not in item.sql and not item.parameters
        assert read_query(item.sql)
    assert 'CAST(NULL AS nvarchar(100)) AS [Code]' in queries(metadata, datetime(2026, 10, 23), counts=False)[0].sql


def test_bad_metadata_and_partition_mismatches_do_not_generate_queries(metadata):
    metadata[DDS]['partitions'][0]['key'] = 'other'
    with pytest.raises(ValidationError, match='Разделители'):
        queries(metadata, datetime(2026, 10, 23))
    metadata[DDS]['partitions'][0]['key'] = 'shared'
    metadata[DDS]['table'] = 'x]; DROP TABLE y;--'
    with pytest.raises(ValidationError):
        queries(metadata, datetime(2026, 10, 23))


def test_choice_export_is_human_readable_safe_for_excel_and_does_not_create_jobs(job, metadata, tmp_path):
    output = tmp_path / 'choices.csv'
    cursor = MagicMock()
    cursor.description = [(name,) for name in ['Reference', 'Code', 'Name', 'DataArea', 'UsageCount']]
    cursor.fetchall.return_value = [(b'\x01' * 16, '0001', '=Не формула', 7, 42)]
    database = MagicMock()
    database.cursor.return_value = cursor
    @contextmanager
    def connection(*args, **kwargs):
        yield database
    command = 'transferdata.erp.control.management.commands.export_cash_receipts_choices'
    with patch(command + '.read_metadata', return_value={'objects': list(metadata.values())}), patch(command + '.connect', connection):
        call_command('export_cash_receipts_choices', output=str(output), as_of='2026-10-23T18:00:00+03:00')
    assert output.read_bytes().startswith(b'\xef\xbb\xbf')
    rows = list(csv.DictReader(output.open(encoding='utf-8-sig'), delimiter=';'))
    assert len(rows) == 3
    assert rows[0]['Наименование'] == "'=Не формула"
    assert rows[0]['Ссылка'] == '0x' + '01' * 16 and rows[0]['ОбластьДанных'] == '7'
    assert rows[0]['Категория'] == rows[0]['ВключитьВОтчет'] == ''
    assert rows[0]['Движений/соответствий'] == '42'
    assert cursor.execute.call_count == 3 and cursor.close.call_count == 3
    assert not job.executions.exists() and not job.source_connection.onec_objects.exists()
    with pytest.raises(CommandError, match='Файл уже существует'):
        call_command('export_cash_receipts_choices', output=str(output))
