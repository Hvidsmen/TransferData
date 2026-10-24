from contextlib import contextmanager
from unittest.mock import MagicMock, patch
import pytest
from django.core.exceptions import ValidationError
from transferdata.python_jobs.handlers import fill_headers, validate_parameters, commercial_headers, HEADERS

COLUMNS = [('DocumentKey', 'nvarchar', 200, None), ('NumberRow', 'int', 4, None), ('Header', 'nvarchar', 4000, None)]


def test_inherit_headers_preserves_unicode_quotes_and_empty_initial_rows():
    assert fill_headers([(1, None), (2, ''), (3, "Блок 'А'"), (4, None), (5, ' '), (6, '')]) == [
        (1, ''), (2, ''), (3, "Блок 'А'"), (4, "Блок 'А'"), (5, ' '), (6, ' ')]
    with pytest.raises(ValueError):
        fill_headers([(1, 'A'), (1, 'B')])
    with pytest.raises(ValueError):
        fill_headers([(3, 'A'), (2, 'B')])


@pytest.mark.parametrize('value', [{'date_from': '01.01.2023'}, {'date_from': None}, {'date_from': '1600-01-01'}, {'other': 1}])
def test_validate_parameters(value):
    with pytest.raises(ValidationError):
        validate_parameters(value)


def test_per_document_transaction_and_parameterized_sql():
    conn, cursor = MagicMock(), MagicMock()
    conn.cursor.return_value = cursor
    cursor.execute.return_value = cursor
    cursor.fetchone.return_value = [0]
    cursor.fetchall.side_effect = [COLUMNS, [('key\'1',), ('key2',)], [(1, "Заголовок 'А'"), (2, None)], [(1, 'Другой')]]
    @contextmanager
    def connect(*args, **kwargs): yield conn
    progress = MagicMock()
    snapshot = {'parameters': {}, 'timeout': 30, 'connection': {'name': 'DWH', 'database': 'DataWH', 'server': 'vm-dwh', 'authentication_type': 'WINDOWS'}}
    with patch('transferdata.python_jobs.handlers.connect', connect):
        assert commercial_headers(snapshot, progress) == 3
    assert conn.commit.call_count == 3  # initial read + two whole documents
    assert conn.rollback.call_count == 0
    calls = cursor.executemany.call_args_list
    assert calls[0].args[1] == [("key'1", 1, "Заголовок 'А'"), ("key'1", 2, "Заголовок 'А'")]
    assert calls[0].args[0].count('?') == 3 and "'А'" not in calls[0].args[0]
    find = cursor.execute.call_args_list[2].args[0]
    assert 'NOT EXISTS' in find and '[NumberRow] = kpt.[НомерСтроки]' in find
    assert progress.call_args.kwargs['completed'] == 2
    assert cursor.close.called


def test_error_rolls_back_current_document_and_is_not_swallowed():
    conn, cursor = MagicMock(), MagicMock()
    conn.cursor.return_value = cursor
    cursor.execute.return_value = cursor
    cursor.fetchone.return_value = [0]
    cursor.fetchall.side_effect = [COLUMNS, [('key',)], [(1, 'text')]]
    cursor.executemany.side_effect = RuntimeError('write failed')
    @contextmanager
    def connect(*args, **kwargs): yield conn
    snapshot = {'parameters': {}, 'timeout': 30, 'connection': {'name': 'DWH', 'database': 'DataWH', 'server': 'vm-dwh', 'authentication_type': 'WINDOWS'}}
    with patch('transferdata.python_jobs.handlers.connect', connect), pytest.raises(RuntimeError):
        commercial_headers(snapshot, MagicMock())
    assert conn.commit.call_count == 1 and conn.rollback.call_count == 1 and cursor.close.called


def test_length_validation_uses_actual_sql_type_bytes_and_never_exposes_values():
    from transferdata.python_jobs.header_validation import validate_rows
    from transferdata.transfer_errors import TransferDataError
    columns = {'DocumentKey': ('nvarchar', 100, None), 'NumberRow': ('int', 4, None), 'Header': ('nvarchar', 4, None)}
    validate_rows(columns, 'key', [(1, '😀')], 17)  # surrogate pair is 4 UTF-16 bytes
    with pytest.raises(TransferDataError) as error:
        validate_rows(columns, 'PRIVATE-DOCUMENT', [(12, 'SECRET')], 17)
    message = str(error.value)
    assert 'Header (nvarchar(2))' in message and 'требуется 12 байт, допустимо 4' in message
    assert '17' in message and '12' in message and '22001' in message
    assert 'SECRET' not in message and 'PRIVATE-DOCUMENT' not in message
    columns['Header'] = ('varchar', 3, 65001)
    validate_rows(columns, 'key', [(1, '€')], 17)
    with pytest.raises(TransferDataError):
        validate_rows(columns, 'key', [(1, '€x')], 17)
    columns['Header'] = ('nvarchar', -1, None)
    validate_rows(columns, 'key', [(1, 'А' * 5000)], 17)


def test_header_preflight_keeps_committed_documents_and_checks_before_delete():
    from transferdata.transfer_errors import TransferDataError
    conn, cursor = MagicMock(), MagicMock()
    conn.cursor.return_value = cursor
    cursor.execute.return_value = cursor
    cursor.fetchone.return_value = [0]
    columns = [*COLUMNS[:2], ('Header', 'nvarchar', 4, None)]
    cursor.fetchall.side_effect = [columns, [('first',), ('second',)], [(1, 'OK')], [(1, 'TOO LONG')]]
    @contextmanager
    def connect(*args, **kwargs): yield conn
    snapshot = {'parameters': {}, 'timeout': 30, 'connection': {'name': 'DWH', 'database': 'DataWH', 'server': 'vm-dwh', 'authentication_type': 'WINDOWS'}}
    progress = MagicMock()
    with patch('transferdata.python_jobs.handlers.connect', connect), pytest.raises(TransferDataError, match='Header'):
        commercial_headers(snapshot, progress)
    assert conn.commit.call_count == 2  # initial query + complete first document
    assert conn.rollback.call_count == 1
    assert cursor.executemany.call_count == 1
    assert sum('DELETE FROM' in call.args[0] for call in cursor.execute.call_args_list) == 1
    assert progress.call_args.kwargs['completed'] == 1 and progress.call_args.kwargs['rows'] == 1


def test_safe_failure_reports_sqlstate_but_never_driver_values():
    import pyodbc
    from transferdata.python_jobs.header_validation import safe_failure
    message = safe_failure(pyodbc.DataError('22003', 'password=secret; value=private'))
    assert '22003' in message and 'Число' in message
    assert 'secret' not in message and 'private' not in message
