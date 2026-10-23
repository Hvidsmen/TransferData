import pytest
from sqlalchemy import Table, MetaData, Column, String, LargeBinary
from sqlalchemy.exc import DataError
from transferdata.transfer_errors import diagnostic, validate_postgres_text, TransferDataError


@pytest.mark.parametrize('state', ['22001', '22003', '22007', '22008', '22021', '22P02', '23502', '23505'])
def test_diagnostics_never_include_driver_values_or_sql(state):
    original = Exception('secret-password INSERT private-row-value')
    original.sqlstate = state
    error = DataError('INSERT INTO private VALUES (?)', {'password': 'secret-password'}, original)
    message, details = diagnostic(error)
    assert details == {'sqlstate': state} and state in message
    assert all(token not in message for token in ('secret-password', 'private-row-value', 'INSERT', 'private VALUES'))


def test_unknown_messages_are_not_serialized():
    assert diagnostic(Exception('secret')) is None
    assert diagnostic(Exception('22003 private row value')) is None
    assert diagnostic(Exception('XX000', 'secret')) is None


def test_nul_diagnostic_identifies_column_without_disclosing_value():
    table = Table('test', MetaData(), Column('Описание', String), Column('binary', LargeBinary))
    with pytest.raises(TransferDataError) as caught:
        validate_postgres_text([{'Описание': 'valid'}, {'Описание': 'secret\x00value'}], table, 1001)
    assert caught.value.details == {'kind': 'nul_character', 'column': 'Описание', 'row_number': 1002}
    assert 'secret' not in str(caught.value) and 'U+0000' in str(caught.value)
    validate_postgres_text([{'Описание': None, 'binary': b'\x00'}], table, 1)


@pytest.mark.django_db
def test_worker_persists_safe_diagnostic_for_run(monkeypatch):
    from unittest.mock import MagicMock
    from transferdata.web.panel.models import Connection, Job, Run
    from transferdata.web.panel import services
    connection = Connection.objects.create(name='diagnostic', target_host='postgres', target_database='analytics')
    job = Job.objects.create(name='ModelCode diagnostic', connection=connection, source_table='ModelCode', target_table='ModelCode')
    run, _ = services.enqueue(job, 'run')
    source, target = MagicMock(), MagicMock()
    monkeypatch.setattr(services, 'engines', lambda config: (source, target))
    def fail(*args, **kwargs):
        raise TransferDataError('Значение не помещается в длину столбца назначения.', sqlstate='22001', batch_first=1001, batch_last=2000)
    monkeypatch.setattr(services, 'transfer', fail)
    services.execute_next()
    run.refresh_from_db()
    assert run.status == 'failed' and run.details['error']['sqlstate'] == '22001'
    assert 'не помещается' in run.message and 'не сохранены' in run.message
    assert source.dispose.called and target.dispose.called


def test_explicit_nul_cleanup_preserves_other_text_binary_and_nulls():
    table = Table('test', MetaData(), Column('Описание', String), Column('binary', LargeBinary))
    payload = [{'Описание': 'А\x00\x00Б', 'binary': b'\x00'}, {'Описание': '\x00'}, {'Описание': None}, {'Описание': 'Текст\nс переносом'}]
    values, characters = validate_postgres_text(payload, table, 1, remove_nul=True)
    assert (values, characters) == (2, 3)
    assert payload == [{'Описание': 'АБ', 'binary': b'\x00'}, {'Описание': ''}, {'Описание': None}, {'Описание': 'Текст\nс переносом'}]
