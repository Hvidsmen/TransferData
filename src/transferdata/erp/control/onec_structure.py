"""Persist a structural preview in the worker; page GETs never query the ERP database."""
import pyodbc
from django.core.exceptions import ValidationError
from transferdata.erp.engine.connections import connect
from transferdata.erp.engine.exceptions import ETLError
from transferdata.erp.reports.additional_attributes import parts, prop, discovery_sql, generate


def enrich_attributes(connection, data):
    if not any(p['name'] == 'ДополнительныеРеквизиты' for p in data.get('table_parts', [])):
        return
    from .onec import read_metadata
    try:
        part = parts(data)
        references = prop(part, 'Свойство').get('references', [])
        if len(references) != 1:
            raise ValidationError('Не определён объект описания свойств.')
        definitions = read_metadata(connection, references[0])
        if (data.get('configuration'), data.get('version')) != (definitions.get('configuration'), definitions.get('version')):
            raise ValidationError('Конфигурация изменилась во время чтения.')
        with connect(connection) as database:
            cursor = database.cursor()
            try:
                cursor.execute(discovery_sql(data, definitions))
                rows = cursor.fetchmany(1001)
            finally:
                cursor.close()
        if len(rows) > 1000:
            raise ValidationError('Слишком много дополнительных свойств.')
        attributes = {}
        for identity, name in rows:
            identity = bytes(identity)
            if identity in attributes and attributes[identity] != name:
                raise ValidationError('Названия свойств различаются по областям данных.')
            attributes[identity] = name
        columns = []
        generate(data, list(attributes.items()), structure=columns)
        data['additional_attributes'] = [
            {'name': name, 'id': identity.hex(), 'columns': [c['name'] for c in columns if c['property_id'] == identity.hex()]}
            for identity, name in sorted(attributes.items(), key=lambda pair: (pair[1] or '').casefold())]
        data['additional_attributes_loaded'] = True
    except (ValidationError, ETLError, pyodbc.Error, TypeError, ValueError, KeyError):
        data['additional_attributes_warning'] = ('Не удалось прочитать названия дополнительных свойств. '
            'Структура табличной части сохранена. Проверьте права SELECT на объект описания свойств '
            'и повторите получение структуры. Значения сотрудников на странице не отображаются.')
