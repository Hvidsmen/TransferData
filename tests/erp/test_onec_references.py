import copy

import pytest
from django.core.exceptions import ValidationError

from transferdata.erp.control.onec import mapping_from_metadata, source_query
from transferdata.erp.control.forms import JobForm
from transferdata.erp.engine.extractor import source_sql

pytestmark = pytest.mark.django_db


@pytest.fixture
def references():
    def prop(name, field, refs, union=False):
        columns = [{'name': field, 'purpose': 'Identity' if union else 'Value', 'type': 'binary(16)'}]
        if union:
            columns += [{'name': field + '_RTRef', 'purpose': 'TypeCode', 'type': 'binary(4)'},
                        {'name': field + '_TYPE', 'purpose': 'Tag', 'type': 'binary(1)'}]
        return {'name': name, 'is_reference': True, 'references': refs, 'columns': columns}
    return {'reference_version': 1, 'name': 'РегистрНакопления.Оплаты', 'table': '_Main',
            'partitions': [{'key': 'tenant', 'source': '_Tenant'}],
            'properties': [
                {'name': 'Область', 'columns': [{'name': '_Tenant', 'purpose': 'Value', 'type': 'int'}]},
                prop('Инвойс', '_Invoice', ['Документ.Инвойс']),
                prop('Партнер', '_Partner', ['Справочник.Партнеры']),
                prop('ТипПлатежа', '_Kind', ['Перечисление.ТипПлатежа']),
                prop('Основание', '_Base', ['Документ.Инвойс', 'Справочник.Партнеры', 'Перечисление.ТипПлатежа'], True),
            ], 'reference_targets': [
                {'name': 'Документ.Инвойс', 'kind': 'Документ', 'type_code': 100,
                 'table': '_Document100', 'id': '_IDRRef', 'date': '_Date_Time', 'number': '_Number',
                 'partitions': [{'key': 'tenant', 'source': '_Tenant'}]},
                {'name': 'Справочник.Партнеры', 'kind': 'Справочник', 'type_code': 101,
                 'table': '_Reference101', 'id': '_IDRRef', 'description': '_Description', 'partitions': []},
                {'name': 'Перечисление.ТипПлатежа', 'kind': 'Перечисление', 'type_code': 102,
                 'values': [
                     {'name': "Безналичный O'Brien", 'hex': '00112233445566778899AABBCCDDEEFF'}]},
            ]}


def test_references_keep_ids_and_resolve_document_catalogue_and_enum(references):
    mapping = mapping_from_metadata(references)
    assert mapping['reference_version'] == 1
    assert not mapping['reference_warnings']
    query = source_query(mapping, adjust_dates=True, resolve_references=True)
    for column in ('ИнвойсСсылка', 'Инвойс', 'ИнвойсДата', 'ИнвойсНомер', 'ПартнерСсылка', 'Партнер',
                   'ТипПлатежаСсылка', 'ТипПлатежа', 'ОснованиеСсылка', 'Основание', 'Основание__Identity',
                   'Основание__Tag', 'Основание__TypeCode'):
        assert ' AS [' + column + ']' in query
    assert '[src].[_Invoice] AS [ИнвойсСсылка]' in query
    assert 'LEFT JOIN [dbo].[_Document100]' in query
    assert '[_Tenant] = [src].[_Tenant]' in query
    assert '[src].[_Base_RTRef] = 0x00000064' in query
    assert '[src].[_Base_RTRef] = 0x00000065' in query
    assert '[src].[_Base_TYPE] = 0x08' in query
    assert "N'Безналичный O''Brien'" in query
    assert "N'Инвойс № '" in query
    assert 'CASE WHEN YEAR([ref1].[_Date_Time]) < 3753' in query
    assert '0x00000000000000000000000000000000' in query
    raw = source_query(mapping)
    assert 'JOIN' not in raw and ' AS [Инвойс]' in raw and 'ИнвойсСсылка' not in raw
    unadjusted = source_query(mapping, resolve_references=True)
    assert 'DATEADD' not in unadjusted


def test_reference_aliases_are_unique_deterministic_and_fit_sql_limit(references):
    references['properties'].append({'name': 'ИнвойсСсылка', 'columns': [
        {'name': '_AlreadyExists', 'purpose': 'Value', 'type': 'nvarchar(20)'}]})
    references['properties'][1]['name'] = 'ДлинныйРеквизит' * 20
    mapping = mapping_from_metadata(references)
    assert mapping == mapping_from_metadata(copy.deepcopy(references))
    aliases = [plan[key] for plan in mapping['references']
               for key in ('raw_alias', 'display_alias', 'date_alias', 'number_alias') if key in plan]
    assert len(aliases) == len(set(a.casefold() for a in aliases))
    assert all(len(a) <= 128 for a in aliases)


def test_unsupported_references_and_missing_partitions_are_visible_not_guessed(references):
    references['reference_targets'][0]['partitions'] = [{'key': 'missing', 'source': '_Tenant'}]
    references['properties'][2]['references'] = ['ЛюбаяСсылка']
    mapping = mapping_from_metadata(references)
    assert any('разделитель' in warning for warning in mapping['reference_warnings'])
    assert any('ЛюбаяСсылка' in warning for warning in mapping['reference_warnings'])
    query = source_query(mapping, resolve_references=True)
    assert '_Document100' not in query
    assert '[src].[_Invoice] AS [Инвойс]' in query
    assert '[src].[_Partner] AS [Партнер]' in query


@pytest.mark.parametrize('field,value', [('table', 'x]; DROP TABLE x;--'), ('id', 'x];--'),
                                        ('description', 'x];--'), ('type_code', '1;DROP')])
def test_reference_metadata_cannot_inject_sql(references, field, value):
    references['reference_targets'][1][field] = value
    with pytest.raises(ValidationError):
        mapping_from_metadata(references)


def test_enum_hex_validated_but_names_are_escaped_literals(references):
    references['reference_targets'][2]['values'][0]['hex'] = '0;DROP'
    with pytest.raises(ValidationError):
        mapping_from_metadata(references)


def test_old_metadata_requires_explicit_refresh_for_enrichment(references):
    del references['reference_version']
    mapping = mapping_from_metadata(references)
    assert 'reference_version' not in mapping
    assert 'LEFT JOIN' not in source_query(mapping)
    with pytest.raises(ValidationError, match='повторно получите структуру'):
        source_query(mapping, resolve_references=True)


def test_reference_form_and_preview_use_the_same_frozen_mapping(job, references):
    from transferdata.erp.control.models import OneCObject
    from transferdata.erp.control.onec import connection_fingerprint
    obj = OneCObject.objects.create(connection=job.source_connection, name=references['name'], kind='РегистрНакопления',
        metadata=references, fingerprint=connection_fingerprint(job.source_connection))
    data = {'name': 'С представлениями', 'source_connection': job.source_connection_id,
            'target_connection': job.target_connection_id, 'source_type': 'ONEC', 'one_c_object': obj.pk,
            'target_schema': 'dbo', 'target_table': 'ReadableInvoice', 'load_mode': 'FULL',
            'batch_size': 1000, 'command_timeout': 300, 'is_active': 'on',
            'onec_adjust_dates': 'on', 'onec_resolve_references': 'on'}
    form = JobForm(data)
    assert form.is_valid(), form.errors
    saved = form.save()
    saved.runnable()
    assert '[ИнвойсСсылка]' in source_sql(saved) and '[ИнвойсДата]' in source_sql(saved)


def test_enumeration_uses_configuration_values_without_a_table_or_reference_column(references):
    enum = references['reference_targets'][2]
    assert 'table' not in enum and 'id' not in enum
    enum['name'] = 'Перечисление.Даичи_ТипыПлатежей'
    references['reference_targets'] = [enum]
    references['properties'] = [references['properties'][3]]
    references['properties'][0]['references'] = [enum['name']]
    mapping = mapping_from_metadata(references)
    assert not mapping['reference_warnings']
    query = source_query(mapping, resolve_references=True)
    assert '[src].[_Kind] AS [ТипПлатежаСсылка]' in query
    assert ' AS [ТипПлатежа]' in query
    assert "THEN N'Безналичный O''Brien'" in query
    assert '0x00112233445566778899AABBCCDDEEFF' in query
    assert 'JOIN' not in query
