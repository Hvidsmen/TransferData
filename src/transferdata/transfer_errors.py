"""Safe transfer diagnostics: never expose driver messages, SQL or row values."""
import re
from sqlalchemy import String

MESSAGES = {
    '22001': 'Значение не помещается в длину столбца назначения. Проверьте длины varchar/char в существующей таблице.',
    '22003': 'Число выходит за диапазон типа назначения. Проверьте integer/bigint и точность numeric.',
    '22007': 'Значение даты или времени имеет недопустимый формат.',
    '22008': 'Дата или время выходит за допустимый диапазон.',
    '22021': 'Недопустимая кодировка текста или нулевой символ в строке.',
    '22P02': 'Значение не соответствует типу назначения: например UUID, число или boolean.',
    '23502': 'В обязательный столбец назначения поступил NULL.',
    '23505': 'Нарушена уникальность ключа в таблице назначения.',
    '23503': 'Нарушена внешняя связь в таблице назначения.',
    '42501': 'Недостаточно прав для операции в базе назначения.',
}


class TransferDataError(Exception):
    def __init__(self, message, **details):
        self.details = details
        super().__init__(message)


def diagnostic(exc):
    if isinstance(exc, TransferDataError):
        return str(exc), exc.details
    original = getattr(exc, 'orig', exc)
    state = getattr(original, 'sqlstate', None) or getattr(original, 'pgcode', None)
    if not state:
        args = getattr(original, 'args', ())
        state = args[0] if args else None
    if not isinstance(state, str) or not re.fullmatch(r'[A-Z0-9]{5}', state):
        return None
    explanation = MESSAGES.get(state)
    if explanation is None and state.startswith('22'):
        explanation = 'Данные не соответствуют типу назначения. Проверьте структуру и значения источника.'
    if explanation is None:
        return None
    return f'{explanation} SQLSTATE: {state}.', {'sqlstate': state}


def validate_postgres_text(payload, target_table, row_start):
    text_names = {c.name for c in target_table.columns if isinstance(c.type, String)}
    for offset, row in enumerate(payload):
        for name in text_names:
            value = row.get(name)
            if isinstance(value, str) and '\x00' in value:
                raise TransferDataError(
                    f'Столбец «{name}»: обнаружен нулевой символ U+0000. PostgreSQL не принимает его в текстовых полях. '
                    'Нужно явно согласовать очистку текста в источнике; значения автоматически не изменялись.',
                    kind='nul_character', column=name, row_number=row_start + offset)
