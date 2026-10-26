import pytest
from django.core.exceptions import ValidationError
from transferdata.erp.reports.individual_contacts import generate
from transferdata.erp.engine.validators import read_query


def root():
    def prop(name, physical):
        return {'name': name, 'columns': [{'name': physical, 'purpose': 'Value', 'type': 'nvarchar(100)'}]}
    return {'name': 'Справочник.ФизическиеЛица', 'table': '_Person', 'reference_targets': [],
        'properties': [prop('Ссылка', '_ID'), prop('ОбластьДанных', '_Area')], 'partitions': [{'key': 'tenant', 'source': '_Area'}],
        'table_parts': [{'name': 'КонтактнаяИнформация', 'table': '_Contacts', 'partitions': [{'key': 'tenant', 'source': '_Area'}],
            'properties': [prop('Ссылка', '_Owner'), prop('НомерСтроки', '_Line'), prop('АдресЭП', '_Email'),
                           prop('НомерТелефона', '_Phone'), prop('ОбластьДанных', '_Area')]}]}


def test_all_contacts_preserved_without_multiplying_person_rows():
    sql = generate(root(), 'SELECT [_ID] AS [Ссылка], [_Area] AS [ОбластьДанных] FROM [_Person]')
    assert read_query(sql) == sql
    assert ' AS [Email]' in sql and ' AS [Телефоны]' in sql
    assert 'AS [КонтактнаяИнформацияJSON]' in sql
    assert 'FOR JSON PATH, INCLUDE_NULL_VALUES' in sql
    assert "FOR XML PATH(''), TYPE).value('.', 'nvarchar(max)')" in sql
    assert '[contact].[ОбластьДанных] = [person].[ОбластьДанных]' in sql
    assert '[contact].[Ссылка] = [person].[Ссылка]' in sql
    assert 'TOP ' not in sql and 'DISTINCT' not in sql
    assert sql.count('ORDER BY [contact].[НомерСтроки]') == 3


def test_missing_contact_metadata_does_not_guess_physical_fields():
    data = root()
    data['table_parts'] = []
    with pytest.raises(ValidationError, match='КонтактнаяИнформация'):
        generate(data, 'SELECT 1')


def test_command_has_separate_destination():
    from transferdata.erp.control.management.commands.prepare_individual_contacts import Command
    options = vars(Command().create_parser('manage', 'prepare_individual_contacts').parse_args([]))
    assert options['with_contacts'] is True
    assert options['object'] == 'Справочник.ФизическиеЛица'
    assert options['target_table'] == 'IndividualsContactsExpanded'
