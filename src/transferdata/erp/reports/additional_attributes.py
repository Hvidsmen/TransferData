"""Flatten discovered 1C additional attributes without choosing or losing duplicate values."""
import hashlib
import re
from django.core.exceptions import ValidationError
from transferdata.erp.control.onec import mapping_from_metadata, source_query
from transferdata.erp.control.onec_references import reference_select
from transferdata.erp.engine.validators import identifier as q


def prop(obj, name):
    matches = [p for p in obj['properties'] if p['name'] == name]
    if len(matches) != 1:
        raise ValidationError(f'Не определён реквизит {name}.')
    return matches[0]


def field(obj, name):
    columns = prop(obj, name)['columns']
    if len(columns) != 1:
        raise ValidationError(f'Составной реквизит {name} требует отдельной настройки.')
    return columns[0]['name']


def parts(root):
    found = [p for p in root.get('table_parts', []) if p['name'] == 'ДополнительныеРеквизиты']
    if len(found) != 1 or not found[0].get('properties'):
        raise ValidationError('Прочитайте полную структуру табличной части ДополнительныеРеквизиты.')
    return {**found[0], 'reference_version': 1, 'reference_targets': root.get('reference_targets', [])}


def same_area(left, right, a, b):
    left = {p['key']: p['source'] for p in left.get('partitions', [])}
    right = {p['key']: p['source'] for p in right.get('partitions', [])}
    if left.keys() != right.keys():
        raise ValidationError('Разделители данных сотрудника, свойств и табличной части не совпадают.')
    return [f'{a}.{q(left[k])} = {b}.{q(right[k])}' for k in sorted(left)]


def discovery_sql(root, definitions):
    part = parts(root)
    conditions = [f'[p].{q(field(part, "Свойство"))} = [d].{q(field(definitions, "Ссылка"))}']
    conditions += same_area(part, definitions, '[p]', '[d]')
    # Include every property used by this table part, including empty values. Never export values here.
    return (f'SELECT DISTINCT [p].{q(field(part, "Свойство"))}, [d].{q(field(definitions, "Наименование"))}'
            f' FROM [dbo].{q(part["table"])} AS [p] LEFT JOIN [dbo].{q(definitions["table"])} AS [d] ON '
            + ' AND '.join(conditions))


def generate(root, attributes):
    part = parts(root)
    base = source_query(mapping_from_metadata(root), adjust_dates=True, resolve_references=True)
    mapping = mapping_from_metadata(part)
    value = prop(part, 'Значение')
    # Limit the reference joins to the value, not the property's definition.
    mapping['references'] = [p for p in mapping['references'] if p['property'] == 'Значение']
    joins, extra, _ = reference_select(mapping, True)
    used = {c['alias'].casefold() for c in mapping_from_metadata(root)['columns']}
    root_mapping = mapping_from_metadata(root)
    for p in root_mapping.get('references', []):
        used.update(p[k].casefold() for k in ('raw_alias', 'display_alias', 'date_alias', 'number_alias') if k in p)
    def alias(name, key):
        name = re.sub(r'[^\w$@# .-]', '_', name).strip() or 'ДопРеквизит'
        if not re.match(r'\w', name[0]):
            name = '_' + name
        candidate = name[:128]
        if candidate.casefold() in used:
            candidate = name[:110] + '_' + hashlib.sha256(key.encode()).hexdigest()[:12]
        if candidate.casefold() in used:
            raise ValidationError('Не удалось сформировать уникальные имена колонок.')
        used.add(candidate.casefold())
        return q(candidate)
    fields = ['[employee].*']
    for identity, name in sorted(attributes, key=lambda x: bytes(x[0])):
        identity = bytes(identity)
        if len(identity) != 16 or not name:
            raise ValidationError('Не найдено название дополнительного свойства; проверьте справочник свойств.')
        key = identity.hex()
        conditions = [f'[src].{q(field(part, "Ссылка"))} = [employee].{q(next(c["alias"] for c in root_mapping["columns"] if c["source"] == field(root, "Ссылка")))}',
                      f'[src].{q(field(part, "Свойство"))} = 0x{key}']
        # The employee derived table exposes aliases rather than physical partition names.
        for condition in same_area(part, root, '[src]', '[employee]'):
            for c in root_mapping['columns']:
                condition = condition.replace('[employee].' + q(c['source']), '[employee].' + q(c['alias']))
            conditions.append(condition)
        def scalar(expression, label):
            fields.append('(SELECT ' + expression + f' FROM [dbo].{q(part["table"])} AS [src] '
                          + ' '.join(joins) + ' WHERE ' + ' AND '.join(conditions) + ') AS ' + alias(label, key + label))
        # A scalar subquery deliberately fails on duplicate properties instead of silently taking MAX/TOP 1.
        display = []
        tag = next((c['name'] for c in value['columns'] if c['purpose'] == 'Tag'), None)
        for column in value['columns']:
            expression = '[src].' + q(column['name'])
            if column.get('is_date') or column.get('purpose') == 'DateTime':
                expression = f'CASE WHEN YEAR({expression}) < 3753 THEN {expression} ELSE DATEADD(year, -2000, {expression}) END'
            suffix = ('Ссылка' if extra else '') if len(value['columns']) == 1 else '__' + column['purpose']
            codes = {'Boolean': '02', 'Numeric': '03', 'DateTime': '04', 'String': '05'}
            if tag and column['purpose'] in codes:
                display.append(f'WHEN [src].{q(tag)} = 0x{codes[column["purpose"]]} THEN CONVERT(nvarchar(max), {expression})')
            scalar(expression, name + suffix)
        for expression in extra:
            expression, output = expression.rsplit(' AS ', 1)
            suffix = output.strip('[]').removeprefix('Значение')
            if not suffix:
                if tag:
                    display.append(f'WHEN [src].{q(tag)} = 0x08 THEN {expression}')
                else:
                    scalar(expression, name)
            else:
                scalar(expression, name + suffix)
        long_text = [p for p in part['properties'] if p['name'] == 'ТекстоваяСтрока']
        if long_text:
            text_field = '[src].' + q(field(part, 'ТекстоваяСтрока'))
            scalar(text_field, name + 'ТекстоваяСтрока')
            if display:
                display.insert(0, f"WHEN NULLIF(CONVERT(nvarchar(max), {text_field}), N'') IS NOT NULL THEN CONVERT(nvarchar(max), {text_field})")
        if display:
            scalar('CASE ' + ' '.join(display) + ' END', name)
    if len(used) > 1000:
        raise ValidationError('Более 1000 колонок: разделите выгрузку дополнительных реквизитов.')
    return 'SELECT\n ' + ',\n '.join(fields) + '\nFROM (\n' + base + '\n) AS [employee]'
