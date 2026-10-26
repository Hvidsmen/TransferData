import pytest
from django.core.exceptions import ValidationError
from transferdata.erp.reports.additional_attributes import discovery_sql, generate
from transferdata.erp.engine.validators import read_query


def prop(name, physical, purpose='Value'):
    return {'name': name, 'columns': [{'name': physical, 'purpose': purpose}]}


def example():
    part = {'name': 'ДополнительныеРеквизиты', 'table': '_Reference649_VT', 'partitions': [{'key': 'area', 'source': '_Area'}],
            'properties': [prop('Ссылка', '_Owner'), prop('Свойство', '_Property'), prop('Область', '_Area'),
                           {'name': 'Значение', 'columns': [
                               {'name': '_Text', 'purpose': 'String'}, {'name': '_Tag', 'purpose': 'Tag'},
                               {'name': '_Number', 'purpose': 'Numeric'}, {'name': '_Date', 'purpose': 'DateTime'}]}]}
    root = {'name': 'Справочник.Сотрудники', 'table': '_Reference649', 'reference_version': 1,
            'reference_targets': [], 'partitions': [{'key': 'area', 'source': '_Area'}],
            'properties': [prop('Ссылка', '_ID'), prop('Наименование', '_Name'), prop('ОбластьДанных', '_Area')], 'table_parts': [part]}
    definitions = {'table': '_Chrc', 'partitions': [{'key': 'area', 'source': '_Tenant'}],
                   'properties': [prop('Ссылка', '_ID'), prop('Наименование', '_Description')]}
    return root, definitions


def test_expands_every_discovered_property_preserves_types_and_area():
    root, _ = example()
    sql = generate(root, [(bytes.fromhex('AB'*16), 'Email'), (bytes.fromhex('CD'*16), 'Телефон')])
    assert read_query(sql) == sql
    assert ' AS [Email]' in sql and ' AS [Телефон]' in sql
    assert ' AS [Email__String]' in sql and ' AS [Email__Tag]' in sql
    assert '[src].[_Tag] = 0x05 THEN CONVERT(nvarchar(max), [src].[_Text])' in sql
    assert '[src].[_Area] = [employee].[ОбластьДанных]' in sql
    assert 'YEAR([src].[_Date]) < 3753' in sql
    assert '0x' + 'ab'*16 in sql
    assert 'TOP ' not in sql and 'MAX(' not in sql and 'GROUP BY' not in sql
    assert sql.count('FROM (') == 1  # outer rows are employees; each property is a scalar subquery


def test_dictionary_comes_from_metadata_and_matches_tenant():
    root, definitions = example()
    sql = discovery_sql(root, definitions)
    assert '[p].[_Property] = [d].[_ID]' in sql
    assert '[p].[_Area] = [d].[_Tenant]' in sql
    assert '[d].[_Description]' in sql
    definitions['partitions'] = []
    with pytest.raises(ValidationError, match='Разделители'):
        discovery_sql(root, definitions)


def test_names_are_safe_unique_and_missing_definitions_fail():
    root, _ = example()
    sql = generate(root, [(b'a'*16, 'Email'), (b'b'*16, 'Email'), (b'c'*16, "x]; DELETE FROM t;--")])
    assert 'DELETE FROM t;' not in sql
    aliases = []
    import re
    aliases = re.findall(r'\) AS \[([^]]+)\]', sql)
    assert len(aliases) == len(set(a.casefold() for a in aliases))
    with pytest.raises(ValidationError, match='название'):
        generate(root, [(b'a'*16, None)])


def test_reference_values_keep_ids_presentations_and_document_fields():
    root, _ = example()
    root['reference_targets'] = [{'name': 'Документ.Заказ', 'kind': 'Документ', 'type_code': 123,
        'table': '_Doc', 'id': '_ID', 'date': '_Date', 'number': '_Num', 'partitions': []}]
    root['table_parts'][0]['properties'][-1] = {'name': 'Значение', 'is_reference': True, 'references': ['Документ.Заказ'], 'columns': [
        {'name': '_Identity', 'purpose': 'Identity'}, {'name': '_Type', 'purpose': 'TypeCode'}, {'name': '_Tag', 'purpose': 'Tag'}]}
    sql = generate(root, [(b'a'*16, 'Заказ')])
    for name in ('Заказ', 'ЗаказСсылка', 'ЗаказДата', 'ЗаказНомер', 'Заказ__Identity', 'Заказ__TypeCode'):
        assert ' AS [' + name + ']' in sql
    assert '= 0x0000007B' in sql and '= 0x08' in sql


def test_individual_command_keeps_separate_defaults():
    from transferdata.erp.control.management.commands.prepare_individual_attributes import Command
    command = Command()
    options = vars(command.create_parser('manage', 'prepare_individual_attributes').parse_args([]))
    assert options['object'] == 'Справочник.ФизическиеЛица'
    assert options['target_table'] == 'IndividualsExpanded'
    assert options['output'] == '.state/IndividualsExpanded.sql'
    assert command.object_label == 'Физические лица'
