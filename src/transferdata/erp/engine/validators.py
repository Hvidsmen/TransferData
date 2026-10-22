import re
import sqlparse
from django.core.exceptions import ValidationError

def identifier(value):
    if not value or len(value) > 128 or not re.fullmatch(r'[\w\u0400-\u04ff][\w\u0400-\u04ff$@#]*', value):
        raise ValidationError('Недопустимое имя SQL-объекта (до 128 символов).')
    return '[' + value + ']'

def table_name(value):
    parts = value.split('.')
    if len(parts) not in (1, 2):
        raise ValidationError('Укажите table или schema.table.')
    return '.'.join(identifier(p) for p in parts)

def read_query(value):
    statements = sqlparse.parse(value)
    if len(statements) != 1 or statements[0].get_type() != 'SELECT':
        raise ValidationError('Разрешён один SELECT (включая CTE).')
    # Defence in depth only. The database account MUST have SELECT-only rights.
    forbidden = {'INTO', 'INSERT', 'UPDATE', 'DELETE', 'MERGE', 'EXEC', 'EXECUTE', 'DROP', 'ALTER', 'CREATE', 'TRUNCATE', 'OPENROWSET', 'OPENQUERY', 'OPENDATASOURCE'}
    for token in statements[0].flatten():
        if token.ttype not in sqlparse.tokens.Comment and token.value.upper() in forbidden:
            raise ValidationError('Запрос содержит запрещённую SQL-команду.')
    return value.strip().rstrip(';')
