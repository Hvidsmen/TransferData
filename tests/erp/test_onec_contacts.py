from unittest.mock import patch
from transferdata.erp.control.onec_contacts import discover_contacts
from transferdata.erp.engine.exceptions import ETLError


def definition(name, field='Представление', references=None):
    return {'name': name, 'table': '_VerifiedTable', 'configuration': 'ERP', 'version': '1', 'properties': [
        {'name': field, 'references': references or [], 'columns': [{'name': '_VerifiedField', 'purpose': 'Value', 'type': 'nvarchar(100)'}]}]}


def test_discovers_table_part_and_register_links_without_personal_records():
    root = definition('Справочник.ФизическиеЛица')
    root['table_parts'] = [dict(definition('КонтактнаяИнформация', 'АдресЭП'), name='КонтактнаяИнформация')]
    linked = definition('РегистрСведений.КонтактнаяИнформация', 'Объект', [root['name']])
    other = definition('РегистрСведений.КонтактыПрочие')
    catalog = {'configuration': 'ERP', 'version': '1', 'objects': [
        {'name': linked['name'], 'kind': 'РегистрСведений'}, {'name': other['name'], 'kind': 'РегистрСведений'}]}
    with patch('transferdata.erp.control.onec_contacts.read_metadata', side_effect=[catalog, {'objects': [linked, other]}]) as reader:
        result = discover_contacts(object(), root)
    assert len(result['sources']) == 3
    assert result['sources'][0]['email_fields'] == ['АдресЭП']
    assert result['sources'][1]['relationship'] == 'reference'
    assert result['sources'][2]['relationship'] == 'unconfirmed'
    assert not result['warnings']
    assert reader.call_count == 2  # batch metadata, never query contact values
    assert result['sources'][0]['fields'][0]['columns'][0]['name'] == '_VerifiedField'


def test_contact_part_is_retained_when_catalogue_search_is_unavailable():
    root = definition('Справочник.ФизическиеЛица')
    root['table_parts'] = [definition('КонтактнаяИнформация')]
    with patch('transferdata.erp.control.onec_contacts.read_metadata', side_effect=ETLError('Нет доступа к метаданным.')):
        result = discover_contacts(object(), root)
    assert len(result['sources']) == 1
    assert result['warnings'] == ['Нет доступа к метаданным.']


def test_changed_configuration_does_not_confirm_unrelated_registers():
    with patch('transferdata.erp.control.onec_contacts.read_metadata', return_value={'configuration': 'Other', 'version': '2'}):
        result = discover_contacts(object(), definition('Справочник.ФизическиеЛица'))
    assert not result['sources']
    assert 'изменилась' in result['warnings'][0]
