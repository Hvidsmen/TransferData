"""Validate fixed header-table fields without logging document values or altering schema."""
from transferdata.transfer_errors import TransferDataError, diagnostic


SQL = '''SELECT c.name, t.name, c.max_length,
 CONVERT(int, COLLATIONPROPERTY(c.collation_name, 'CodePage'))
 FROM sys.columns AS c INNER JOIN sys.types AS t ON t.user_type_id = c.system_type_id
 WHERE c.object_id = OBJECT_ID(?) AND c.name IN (N'DocumentKey', N'NumberRow', N'Header')'''


def read_columns(cursor, table):
    result = {name: (kind.lower(), length, codepage) for name, kind, length, codepage in cursor.execute(SQL, table).fetchall()}
    if set(result) != {'DocumentKey', 'NumberRow', 'Header'}:
        raise TransferDataError('Не удалось прочитать структуру DocumentKey, NumberRow и Header. Проверьте таблицу назначения и права просмотра её метаданных.')
    return result


def validate_rows(columns, key, prepared, document):
    ranges = {'tinyint': (0, 255), 'smallint': (-32768, 32767),
              'int': (-2147483648, 2147483647), 'bigint': (-9223372036854775808, 9223372036854775807)}
    for number, header in prepared:
        for column, value in [('DocumentKey', key), ('NumberRow', number), ('Header', header)]:
            kind, limit, codepage = columns[column]
            location = f'Документ по порядку: {document}; строка заказа: {number}. '
            if column == 'NumberRow' and kind in ranges:
                lo, hi = ranges[kind]
                if not lo <= value <= hi or int(value) != value:
                    raise TransferDataError(location + f'Столбец NumberRow: номер строки не помещается в {kind}. SQLSTATE: 22003.', sqlstate='22003')
            if not isinstance(value, str) or limit == -1:
                continue
            if kind in ('nvarchar', 'nchar'):
                encoding = 'utf-16-le'
            elif kind in ('varchar', 'char') and codepage:
                encoding = 'utf-8' if codepage == 65001 else f'cp{codepage}'
            else:
                continue  # Legacy text/ntext and other types are checked by SQL Server.
            try:
                actual = len(value.encode(encoding))
            except LookupError:
                continue
            except UnicodeError:
                raise TransferDataError(location + f'Столбец {column}: текст не представим в кодировке {kind}. Проверьте Unicode-тип назначения. SQLSTATE: 22021.', sqlstate='22021') from None
            if actual > limit:
                size = f'{limit // 2}' if kind in ('nvarchar', 'nchar') else str(limit)
                raise TransferDataError(location + f'Столбец {column} ({kind}({size})): требуется {actual} байт, допустимо {limit}. '
                    'Исправьте исходный текст или согласуйте увеличение столбца. Текст не обрезан; размеры столбцов автоматически не меняются. SQLSTATE: 22001.', sqlstate='22001')


def safe_failure(exc):
    known = diagnostic(exc)
    explanation = known[0] if known else f'Ошибка {type(exc).__name__}. Проверьте структуру таблиц, подключение и права записи.'
    return 'Обработчик заголовков: ' + explanation + ' Текущий документ не сохранён; ранее зафиксированные документы сохранены.'
