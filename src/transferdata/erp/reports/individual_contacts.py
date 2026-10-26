"""Append contact summaries and lossless contact rows to a one-row-per-person query."""
from django.core.exceptions import ValidationError
from transferdata.erp.control.onec import mapping_from_metadata, source_query
from transferdata.erp.engine.validators import identifier as q
from .additional_attributes import field, same_area


def generate(root, employees_sql):
    found = [p for p in root.get('table_parts', []) if p['name'] == 'КонтактнаяИнформация' and p.get('properties')]
    if len(found) != 1:
        raise ValidationError('Не найдена полная структура табличной части КонтактнаяИнформация.')
    part = {**found[0], 'name': root['name'] + '.КонтактнаяИнформация',
            'reference_version': 1, 'reference_targets': root.get('reference_targets', [])}
    mapping = mapping_from_metadata(part)
    parent = mapping_from_metadata(root)
    def alias(metadata, physical):
        return next(c['alias'] for c in metadata['columns'] if c['source'] == physical)
    contact_sql = source_query(mapping, adjust_dates=True, resolve_references=True)
    conditions = [f'[contact].{q(alias(mapping, field(part, "Ссылка")))} = [person].{q(alias(parent, field(root, "Ссылка")))}']
    for condition in same_area(part, root, '[contact]', '[person]'):
        for metadata, prefix in ((mapping, '[contact].'), (parent, '[person].')):
            for column in metadata['columns']:
                condition = condition.replace(prefix + q(column['source']), prefix + q(column['alias']))
        conditions.append(condition)
    where = ' AND '.join(conditions)
    source = '(\n' + contact_sql + '\n) AS [contact]'
    line = '[contact].' + q(alias(mapping, field(part, 'НомерСтроки')))
    fields = ['[person].*']
    for name, output in [('АдресЭП', 'Email'), ('НомерТелефона', 'Телефоны')]:
        value = '[contact].' + q(alias(mapping, field(part, name)))
        fields.append(f'''STUFF((SELECT N'; ' + CONVERT(nvarchar(max), {value})
 FROM {source} WHERE {where} AND NULLIF(LTRIM(RTRIM({value})), N'') IS NOT NULL
 ORDER BY {line} FOR XML PATH(''), TYPE).value('.', 'nvarchar(max)'), 1, 2, N'') AS {q(output)}''')
    fields.append(f'''(SELECT [contact].* FROM {source} WHERE {where}
 ORDER BY {line} FOR JSON PATH, INCLUDE_NULL_VALUES) AS [КонтактнаяИнформацияJSON]''')
    return 'SELECT\n ' + ',\n '.join(fields) + '\nFROM (\n' + employees_sql + '\n) AS [person]'
