"""Discover contact storage from configuration metadata, never from personal values."""
from django.core.exceptions import ValidationError
from transferdata.erp.control.onec import read_metadata, mapping_from_metadata
from transferdata.erp.engine.exceptions import ETLError


def contact_name(value):
    value = value.casefold()
    return any(word in value for word in ('контакт', 'электронн', 'email', 'e_mail'))


def describe_source(root, data, *, owner=None):
    mapping = mapping_from_metadata(data)
    fields = []
    for prop in data['properties']:
        fields.append({'name': prop['name'], 'type': prop.get('type', ''),
                       'references': prop.get('references', []),
                       'columns': [{'name': c['source'], 'alias': c['alias'], 'type': c['type'], 'purpose': c['purpose']}
                                   for c in mapping['columns'] if c['property'] == prop['name']]})
    links = [{'property': p['name'], 'columns': p['columns']}
             for p in data['properties'] if root['name'] in p.get('references', [])]
    return {'name': data['name'], 'table': data['table'], 'fields': fields,
            'relationship': 'table_part' if owner else 'reference' if links else 'unconfirmed',
            'links': links, 'owner': owner,
            'email_fields': [p['name'] for p in data['properties'] if any(w in p['name'].casefold()
                              for w in ('email', 'e_mail', 'электронн', 'адресэп'))]}


def discover_contacts(connection, root):
    result = {'sources': [], 'warnings': [], 'searched': True}
    direct = [p for p in root.get('properties', []) if contact_name(p['name']) or 'адресэп' in p['name'].casefold()]
    if direct:
        result['sources'].append(describe_source(root, {**root, 'properties': direct}, owner=root['name']))
    for part in root.get('table_parts', []):
        if contact_name(part['name']):
            if not part.get('properties'):
                result['warnings'].append('Табличная часть ' + part['name'] + ': полная структура не прочитана.')
                continue
            result['sources'].append(describe_source(root, {**part, 'name': root['name'] + '.' + part['name']}, owner=root['name']))
    try:
        catalogue = read_metadata(connection)
        if (catalogue.get('configuration'), catalogue.get('version')) != (root.get('configuration'), root.get('version')):
            raise ETLError('Конфигурация изменилась во время поиска; повторите получение структуры.')
        candidates = sorted({item['name'] for item in catalogue.get('objects', [])
                             if item.get('kind') == 'РегистрСведений' and contact_name(item['name'])})
        if len(candidates) > 32:
            result['warnings'].append('Найдено более 32 регистров-кандидатов; показаны первые 32. Поиск неполный.')
        if candidates:
            # One helper invocation loads the configuration once for all candidates.
            batch = read_metadata(connection, names=candidates[:32])
            found = {item.get('name'): item for item in batch.get('objects', [])}
            for name in candidates[:32]:
                try:
                    data = found.get(name, {})
                    if data.get('name') != name or (data.get('configuration'), data.get('version')) != (root.get('configuration'), root.get('version')):
                        raise ETLError('Структура не соответствует текущей конфигурации.')
                    result['sources'].append(describe_source(root, data))
                except (ETLError, ValidationError):
                    result['warnings'].append('Не удалось прочитать структуру ' + name + '. Проверьте доступ и повторите поиск.')
    except ETLError as exc:
        result['warnings'].append(str(exc))
    return result
