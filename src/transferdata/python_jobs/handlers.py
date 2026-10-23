"""Built-in handlers. SQL identifiers are fixed; all values are bound parameters."""
from datetime import date
from django.core.exceptions import ValidationError
from transferdata.erp.control.models import DatabaseConnection
from transferdata.erp.engine.connections import connect

DOCUMENTS = '[erp].[Документ.бит_КоммерческоеПредложениеКлиенту]'
LINES = '[erp].[Документы.бит_КоммерческоеПредложениеКлиентуТовары]'
HEADERS = '[erp].[Документы.бит_КоммерческоеПредложениеКлиентуТоварыHeaders]'


def validate_parameters(parameters):
    if set(parameters) - {'date_from'}:
        raise ValidationError({'parameters': 'Допустим только параметр date_from, например 2023-01-01.'})
    try:
        value = date.fromisoformat(parameters.get('date_from', '2023-01-01'))
        if value.year < 1753:
            raise ValueError
        return value
    except (ValueError, TypeError):
        raise ValidationError({'parameters': 'date_from должен быть датой YYYY-MM-DD, не раньше 1753 года.'}) from None


def fill_headers(rows):
    """Keep the last nonempty header, including Unicode and apostrophes; reject ambiguous line order."""
    previous = None
    header = ''
    result = []
    for number, text in rows:
        if number is None or (previous is not None and number <= previous):
            raise ValueError('Неоднозначный порядок строк документа')
        previous = number
        if text is not None and text != '':
            if not isinstance(text, str):
                raise ValueError('Заголовок должен быть строкой')
            header = text
        result.append((number, header))
    return result


def connection_from_snapshot(values):
    # Treat this as the existing profile captured at enqueue time, not a new row
    # whose id/name would fail Django's uniqueness validation. Never save it.
    names = [field.attname for field in DatabaseConnection._meta.concrete_fields if field.attname in values]
    return DatabaseConnection.from_db('default', names, [values[name] for name in names])


def commercial_headers(snapshot, progress):
    start_date = validate_parameters(snapshot['parameters'])
    config = connection_from_snapshot(snapshot['connection'])
    if config.database.casefold() != 'datawh':
        raise ValueError('Ожидается DataWH')
    progress(completed=0, total=None, rows=0, message='Проверяем подключение и ищем необработанные документы')
    with connect(config, timeout=min(snapshot['timeout'], 86400)) as conn:
        cursor = conn.cursor()
        try:
            # Session lock also protects against another job/portal instance processing the same table.
            lock = cursor.execute("DECLARE @result int; EXEC @result = sys.sp_getapplock @Resource=N'etl-dev:commercial-headers', @LockMode='Exclusive', @LockOwner='Session', @LockTimeout=0; SELECT @result").fetchone()[0]
            if lock < 0:
                raise RuntimeError('Обработчик заголовков уже выполняется')
            # Unlike NOT IN this handles NULL keys in the target. Missing individual rows repair partial documents.
            keys = cursor.execute(f'''SELECT DISTINCT kp.[DocumentKey]
FROM {DOCUMENTS} AS kp INNER JOIN {LINES} AS kpt ON kp.[Ссылка] = kpt.[Ссылка]
WHERE kp.[Дата] >= ? AND kp.[DocumentKey] IS NOT NULL
AND NOT EXISTS (SELECT 1 FROM {HEADERS} AS h
 WHERE h.[DocumentKey] = kp.[DocumentKey] AND h.[NumberRow] = kpt.[НомерСтроки])
ORDER BY kp.[DocumentKey]''', start_date).fetchall()
            conn.commit()
            total = len(keys)
            count = 0
            progress(completed=0, total=total, rows=0, message=f'Найдено документов: {total}')
            for completed, (key,) in enumerate(keys, 1):
                try:
                    raw = cursor.execute(f'''SELECT kpt.[НомерСтроки], kpt.[ТекстЗаголовка]
FROM {DOCUMENTS} AS kp INNER JOIN {LINES} AS kpt ON kp.[Ссылка] = kpt.[Ссылка]
WHERE kp.[DocumentKey] = ? AND kp.[Дата] >= ? ORDER BY kpt.[НомерСтроки]''', key, start_date).fetchall()
                    prepared = fill_headers(raw)
                    if not prepared:
                        raise ValueError('Документ исчез во время обработки')
                    cursor.execute(f'DELETE FROM {HEADERS} WHERE [DocumentKey] = ?', key)
                    cursor.executemany(f'INSERT INTO {HEADERS} ([DocumentKey], [NumberRow], [Header]) VALUES (?, ?, ?)',
                                       [(key, number, header) for number, header in prepared])
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
                count += len(prepared)
                progress(completed=completed, total=total, rows=count, message=f'Документы: {completed} из {total}; записано строк: {count}')
            return count
        finally:
            cursor.close()
