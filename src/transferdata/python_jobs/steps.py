"""SQL procedure step definitions; values are bound parameters, never SQL fragments."""
import re
import json
from django.core.exceptions import ValidationError
from transferdata.erp.engine.validators import identifier


def validate_steps(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 64:
        raise ValidationError('Добавьте от 1 до 64 шагов.')
    try:
        if len(json.dumps(value, allow_nan=False).encode()) > 262144:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValidationError('Шаги должны содержать корректный JSON размером до 256 КБ.') from None
    ids, positions = set(), {}
    for index, step in enumerate(value):
        if not isinstance(step, dict) or set(step) - {'id', 'name', 'database', 'schema', 'procedure', 'enabled', 'depends_on', 'parameters', 'timeout'}:
            raise ValidationError('Некорректная структура шага.')
        key = step.get('id', '')
        if not isinstance(key, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', key) or key in ids:
            raise ValidationError('Идентификаторы шагов должны быть уникальными.')
        ids.add(key); positions[key] = index
        if not isinstance(step.get('name'), str) or not step['name'].strip() or len(step['name']) > 150:
            raise ValidationError('Укажите название каждого шага (до 150 символов).')
        for field in ('database', 'schema', 'procedure'):
            item = step.get(field, '' if field == 'database' else 'dbo' if field == 'schema' else '')
            if not isinstance(item, str):
                raise ValidationError('База, схема и процедура должны быть строками.')
            if field != 'database' or item:
                identifier(item)
        if type(step.get('enabled', True)) is not bool or type(step.get('timeout', 300)) is not int or not 1 <= step.get('timeout', 300) <= 86400:
            raise ValidationError('Проверьте включение и таймаут шага.')
        parameters = step.get('parameters', {})
        if not isinstance(parameters, dict) or len(parameters) > 64 or any(
            not isinstance(k, str) or not re.fullmatch(r'@?\w{1,128}', k) or (v is not None and type(v) not in (str, int, float, bool))
            for k, v in parameters.items()):
            raise ValidationError('Параметры процедуры: JSON-объект скалярных значений; SQL-фрагменты не допускаются.')
        normalized = [k.lstrip('@').casefold() for k in parameters]
        if len(set(normalized)) != len(normalized):
            raise ValidationError('Имена параметров процедуры повторяются.')
        dependencies = step.get('depends_on', [])
        if not isinstance(dependencies, list) or any(not isinstance(k, str) for k in dependencies) or len(set(dependencies)) != len(dependencies):
            raise ValidationError('Проверьте зависимости шага.')
    for index, step in enumerate(value):
        for key in step.get('depends_on', []):
            if key not in positions or positions[key] >= index:
                raise ValidationError(f'Шаг «{step["name"]}» зависит от отсутствующего или более позднего шага. Измените порядок.')
            if step.get('enabled', True) and not value[positions[key]].get('enabled', True):
                raise ValidationError(f'Нельзя выполнить «{step["name"]}»: обязательный предыдущий шаг отключён.')
    if not any(s.get('enabled', True) for s in value):
        raise ValidationError('Включите хотя бы один шаг.')
    return value


def procedure_sql(step, database):
    parts = [step.get('database') or database, step.get('schema', 'dbo'), step['procedure']]
    parameters = step.get('parameters', {})
    sql = 'DECLARE @__etl_step_code int; EXEC @__etl_step_code = ' + '.'.join(identifier(p) for p in parts)
    if parameters:
        sql += ' ' + ', '.join('@' + name.lstrip('@') + ' = ?' for name in parameters)
    sql += '; SELECT @__etl_step_code AS [__etl_step_return_code];'
    return sql, list(parameters.values())
