"""One-level reference presentations from verified metadata, with original IDs retained."""
import hashlib
import re

from django.core.exceptions import ValidationError
from transferdata.erp.engine.validators import identifier


def literal(value):
    return "N'" + str(value).replace("'", "''") + "'"


def reference_mapping(data, columns):
    targets = {}
    for target in data.get('reference_targets', []):
        name = target['name']
        if name in targets or target['kind'] not in ('Справочник', 'Документ', 'Перечисление'):
            raise ValidationError('Некорректный тип целевого объекта ссылки.')
        code = target['type_code']
        if type(code) is not int or not 0 < code <= 2147483647:
            raise ValidationError('Некорректный код типа ссылки.')
        for field in ('table', 'id', 'description', 'date', 'number'):
            if target.get(field):
                identifier(target[field])
        if target['kind'] != 'Перечисление' and (not target.get('table') or not target.get('id')):
            raise ValidationError('Отсутствует таблица или идентификатор целевого объекта ссылки.')
        for value in target.get('values') or []:
            if not re.fullmatch('[0-9A-Fa-f]{32}', value['hex']) or not isinstance(value['name'], str):
                raise ValidationError('Некорректное значение перечисления.')
        targets[name] = target
    partitions = {part['key']: part['source'] for part in data.get('partitions', [])}
    for field in partitions.values():
        identifier(field)
    used = {column['alias'].casefold() for column in columns}

    def allocate(name):
        name = re.sub(r'[^\w$@#]', '_', name)
        if not name or not re.match(r'\w', name[0]):
            name = '_' + name
        candidate, n = name[:128], 0
        while candidate.casefold() in used:
            candidate = name[:119] + '_' + hashlib.sha256(f'{name}:{n}'.encode()).hexdigest()[:8]
            n += 1
        used.add(candidate.casefold())
        return candidate

    plans, warnings = [], []
    for prop in data['properties']:
        if not prop.get('is_reference'):
            continue
        name = prop['name']
        parts = prop['columns']
        identity = next((p['name'] for p in parts if p['purpose'] == 'Identity'), None)
        if not identity and len(parts) == 1:
            identity = parts[0]['name']
        if not identity:
            warnings.append(f'{name}: не определён столбец идентификатора; сохранены исходные компоненты.')
            continue
        source = next(c for c in columns if c['source'] == identity)
        tag = next((p['name'] for p in parts if p['purpose'] == 'Tag'), None)
        type_field = next((p['name'] for p in parts if p['purpose'] == 'TypeCode'), None)
        selected, codes = [], set()
        for target_name in prop.get('references', []):
            target = targets.get(target_name)
            if not target:
                warnings.append(f'{name}: тип {target_name} не поддерживает представление; ссылка сохранена.')
                continue
            if not type_field and len(prop['references']) != 1:
                warnings.append(f'{name}: отсутствует код составного типа; представление не вычислено.')
                continue
            if target['type_code'] in codes:
                raise ValidationError('Повторяющиеся коды типов в описании ссылки.')
            codes.add(target['type_code'])
            conditions = []
            missing_partition = False
            for part in target.get('partitions', []):
                identifier(part['source'])
                if part['key'] not in partitions:
                    missing_partition = True
                    break
                conditions.append({'source': partitions[part['key']], 'target': part['source']})
            if missing_partition:
                warnings.append(f'{name}: для {target_name} отсутствует разделитель данных; ссылка сохранена.')
                continue
            if target['kind'] == 'Справочник' and not target.get('description'):
                warnings.append(f'{name}: {target_name} не имеет наименования; ссылка сохранена.')
                continue
            if target['kind'] == 'Документ' and (not target.get('date') or not target.get('number')):
                warnings.append(f'{name}: у {target_name} не определены дата и номер; ссылка сохранена.')
                continue
            selected.append({**target, 'conditions': conditions})
        if not selected:
            if not prop.get('references'):
                warnings.append(f'{name}: тип ссылки не определён; сохранены исходные компоненты.')
            continue
        # For scalar references the old property alias becomes the presentation;
        # the original bytes move to an explicit <property>Ссылка column.
        scalar = len(parts) == 1
        if scalar:
            used.remove(source['alias'].casefold())
        base = name[:-6] if name.endswith('Ссылка') and len(name) > 6 else name
        plan = {'property': name, 'source': identity, 'tag': tag, 'type_field': type_field,
                'targets': selected, 'scalar': scalar, 'raw_alias': allocate(base + 'Ссылка'),
                'display_alias': allocate(base)}
        if any(t['kind'] == 'Документ' for t in selected):
            plan.update(date_alias=allocate(base + 'Дата'), number_alias=allocate(base + 'Номер'))
        plans.append(plan)
    return plans, warnings


def reference_select(mapping, adjust_dates):
    """LEFT JOINs keep unmatched/empty references; discriminator and tenant constrain joins."""
    plans = mapping['references']
    joins, extra, raw_aliases = [], [], {}
    count = 0
    base = '[src].'
    def date(value):
        if not adjust_dates:
            return value
        return f'CASE WHEN YEAR({value}) < 3753 THEN {value} ELSE DATEADD(year, -2000, {value}) END'
    def coalesce(values, sql_type):
        if not values:
            return f'CAST(NULL AS {sql_type})'
        return values[0] if len(values) == 1 else 'COALESCE(' + ', '.join(values) + ')'
    for plan in plans:
        displays, dates, numbers = [], [], []
        identity = base + identifier(plan['source'])
        if plan['scalar']:
            raw_aliases[plan['source']] = plan['raw_alias']
        else:
            extra.append(identity + ' AS ' + identifier(plan['raw_alias']))
        for target in plan['targets']:
            conditions = [identity + ' <> 0x00000000000000000000000000000000']
            if plan['tag']:
                conditions.append(base + identifier(plan['tag']) + ' = 0x08')
            if plan['type_field']:
                conditions.append(base + identifier(plan['type_field']) + f" = 0x{target['type_code']:08X}")
            if target['kind'] == 'Перечисление':
                values = target.get('values') or []
                if values:
                    cases = ' '.join('WHEN 0x' + value['hex'] + ' THEN ' + literal(value['name']) for value in values)
                    displays.append('CASE WHEN ' + ' AND '.join(conditions) + ' THEN CONVERT(nvarchar(max), CASE '
                                    + identity + ' ' + cases + ' END) END')
                continue
            count += 1
            if count > 200:
                raise ValidationError('SELECT требует более 200 связей. Выключите расшифровку ссылок или используйте отдельный SQL-запрос с нужными полями.')
            alias = identifier('ref' + str(count))
            field = lambda name: alias + '.' + identifier(target[name])
            conditions.insert(0, field('id') + ' = ' + identity)
            for partition in target['conditions']:
                conditions.append(alias + '.' + identifier(partition['target']) + ' = ' + base + identifier(partition['source']))
            joins.append('LEFT JOIN [dbo].' + identifier(target['table']) + ' AS ' + alias + ' ON ' + ' AND '.join(conditions))
            if target['kind'] == 'Справочник':
                displays.append('CONVERT(nvarchar(max), ' + field('description') + ')')
            else:
                corrected = date(field('date'))
                dates.append(corrected)
                numbers.append('CONVERT(nvarchar(100), ' + field('number') + ')')
                displays.append('CASE WHEN ' + field('id') + ' IS NOT NULL THEN CONVERT(nvarchar(max), CONCAT('
                                + literal(target['name'].split('.', 1)[-1] + ' № ') + ', '
                                + field('number') + ", N' от ', CONVERT(nvarchar(10), " + corrected + ', 104))) END')
        extra.append(coalesce(displays, 'nvarchar(max)') + ' AS ' + identifier(plan['display_alias']))
        if 'date_alias' in plan:
            extra.append(coalesce(dates, 'datetime2') + ' AS ' + identifier(plan['date_alias']))
            extra.append(coalesce(numbers, 'nvarchar(100)') + ' AS ' + identifier(plan['number_alias']))
    return joins, extra, raw_aliases
