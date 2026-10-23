from contextlib import contextmanager
from unittest.mock import MagicMock, patch
import pytest
from django.core.exceptions import ValidationError
from transferdata.python_jobs.handlers import fill_headers, validate_parameters, commercial_headers, HEADERS


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
    cursor.fetchall.side_effect = [[('key\'1',), ('key2',)], [(1, "Заголовок 'А'"), (2, None)], [(1, 'Другой')]]
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
    find = cursor.execute.call_args_list[1].args[0]
    assert 'NOT EXISTS' in find and '[NumberRow] = kpt.[НомерСтроки]' in find
    assert progress.call_args.kwargs['completed'] == 2
    assert cursor.close.called


def test_error_rolls_back_current_document_and_is_not_swallowed():
    conn, cursor = MagicMock(), MagicMock()
    conn.cursor.return_value = cursor
    cursor.execute.return_value = cursor
    cursor.fetchone.return_value = [0]
    cursor.fetchall.side_effect = [[('key',)], [(1, 'text')]]
    cursor.executemany.side_effect = RuntimeError('write failed')
    @contextmanager
    def connect(*args, **kwargs): yield conn
    snapshot = {'parameters': {}, 'timeout': 30, 'connection': {'name': 'DWH', 'database': 'DataWH', 'server': 'vm-dwh', 'authentication_type': 'WINDOWS'}}
    with patch('transferdata.python_jobs.handlers.connect', connect), pytest.raises(RuntimeError):
        commercial_headers(snapshot, MagicMock())
    assert conn.commit.call_count == 1 and conn.rollback.call_count == 1 and cursor.close.called
