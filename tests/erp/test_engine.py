from contextlib import contextmanager
from decimal import Decimal
from unittest.mock import Mock, patch
import pytest
from transferdata.erp.engine.extractor import batches, metadata
from transferdata.erp.engine.transformer import Column, sql_type, transform_batch
from transferdata.erp.engine.loader import Loader
from transferdata.erp.engine.runner import run
from transferdata.erp.engine.exceptions import ETLError
from transferdata.erp.engine.connections import connect, test_connection as check_connection

def test_streaming_fetchmany():
    cursor = Mock()
    cursor.fetchmany.side_effect = [[(1,), (2,)], [(3,)], []]
    assert list(batches(cursor, 2)) == [[(1,), (2,)], [(3,)]]
    assert cursor.fetchmany.call_count == 3
    cursor.fetchall.assert_not_called()

@pytest.mark.parametrize('name', ['int', 'bigint', 'decimal(38,18)', 'nvarchar(max)', 'datetime2(7)', 'bit', 'uniqueidentifier', 'varbinary(16)'])
def test_types(name):
    assert sql_type(name) == name

@pytest.mark.parametrize('name', ['xml', 'geography', 'timestamp', 'dbo.MyAlias', 'int); DROP TABLE t;--'])
def test_unsupported_type(name):
    with pytest.raises(ETLError): sql_type(name)

def test_native_values_unchanged():
    rows = [(Decimal('12345678901234567890.123456789'), b'\x00\xff', None)]
    assert transform_batch(rows) is rows

def test_metadata_closed_and_typed():
    conn = Mock()
    cursor = conn.cursor.return_value
    cursor.description = [(x,) for x in ['name', 'system_type_name', 'is_nullable', 'is_hidden']]
    cursor.fetchall.return_value = [('amount', 'decimal(38,18)', False, False), ('ref', 'varbinary(16)', True, False)]
    assert metadata(conn, 'SELECT amount, ref FROM t') == [Column('amount', 'decimal(38,18)', False), Column('ref', 'varbinary(16)', True)]
    cursor.close.assert_called_once()

@pytest.fixture
def loader():
    conn = Mock()
    obj = Loader(conn, 'dbo', 'report', [Column('x', 'int', True)])
    return obj

def test_batch_write(loader):
    loader.write([(1,), (None,)])
    cursor = loader.conn.cursor.return_value
    assert cursor.fast_executemany is True
    args = cursor.executemany.call_args.args
    assert 'INSERT INTO [dbo].[_etl_' in args[0]
    assert args[1] == [(1,), (None,)]
    loader.conn.commit.assert_called_once()

def test_atomic_existing_publication(loader):
    cursor = loader.conn.cursor.return_value
    cursor.fetchone.side_effect = [(2,), (42,), (0,), (2,)]
    with patch('transferdata.erp.engine.loader.metadata', return_value=loader.columns):
        loader.publish(2)
    statements = [call.args[0] for call in cursor.execute.call_args_list]
    delete = next(i for i,s in enumerate(statements) if s.startswith('DELETE'))
    insert = next(i for i,s in enumerate(statements) if s.startswith('INSERT'))
    assert delete < insert
    loader.conn.commit.assert_called_once()
    loader.conn.rollback.assert_not_called()

def test_incompatible_target_untouched(loader):
    cursor = loader.conn.cursor.return_value
    cursor.fetchone.side_effect = [(2,), (42,)]
    with patch('transferdata.erp.engine.loader.metadata', return_value=[Column('x', 'bigint', True)]):
        with pytest.raises(ETLError): loader.publish(2)
    assert not any(call.args[0].startswith('DELETE') for call in cursor.execute.call_args_list)
    loader.conn.rollback.assert_called_once()
    loader.conn.commit.assert_not_called()

def test_stage_count_failure_never_deletes(loader):
    cursor = loader.conn.cursor.return_value
    cursor.fetchone.return_value = (1,)
    with pytest.raises(ETLError): loader.publish(2)
    assert not any(call.args[0].startswith('DELETE') for call in cursor.execute.call_args_list)
    loader.conn.rollback.assert_called_once()

def test_insert_failure_rolls_back_working_table(loader):
    # Simulate transactional SQL Server semantics and prove DELETE is rolled back.
    working = [(99,)]
    pending = list(working)
    cursor = loader.conn.cursor.return_value
    cursor.fetchone.side_effect = [(2,), (42,), (0,)]
    def sql(statement, *args):
        if statement.startswith('DELETE'): pending.clear()
        if statement.startswith('INSERT'): raise RuntimeError('disk full')
        return cursor
    def rollback(): pending[:] = working
    cursor.execute.side_effect = sql
    loader.conn.rollback.side_effect = rollback
    with patch('transferdata.erp.engine.loader.metadata', return_value=loader.columns):
        with pytest.raises(RuntimeError): loader.publish(2)
    assert pending == working == [(99,)]
    loader.conn.commit.assert_not_called()
    loader.conn.rollback.assert_called_once()

def test_new_table_transaction(loader):
    cursor = loader.conn.cursor.return_value
    cursor.fetchone.side_effect = [(2,), (None,), (2,)]
    loader.publish(2)
    assert any(' INTO [dbo].[report] FROM ' in call.args[0] for call in cursor.execute.call_args_list)
    loader.conn.commit.assert_called_once()

@pytest.mark.django_db
def test_engine_streams_and_closes(job):
    source, target = Mock(), Mock()
    source.cursor.return_value.fetchmany.side_effect = [[(1,)], [(2,)], []]
    @contextmanager
    def fake_connect(config, timeout):
        try: yield source if config.pk == job.source_connection_id else target
        finally: (source if config.pk == job.source_connection_id else target).close()
    with patch('transferdata.erp.engine.runner.connect', fake_connect), patch('transferdata.erp.engine.runner.metadata', return_value=[Column('x', 'int', True)]), patch('transferdata.erp.engine.runner.Loader') as cls:
        progress = Mock()
        assert run(job, progress) == 2
        assert cls.return_value.write.call_count == 2
        cls.return_value.publish.assert_called_once_with(2)
        cls.return_value.cleanup.assert_called_once()
    source.close.assert_called_once()
    target.close.assert_called_once()

@pytest.mark.django_db
@pytest.mark.parametrize('step', ['execute', 'write'])
def test_engine_failure_never_publishes(job, step):
    source = Mock()
    source.cursor.return_value.fetchmany.side_effect = [[(1,)], []]
    @contextmanager
    def fake_connect(*args): yield source
    with patch('transferdata.erp.engine.runner.connect', fake_connect), patch('transferdata.erp.engine.runner.metadata', return_value=[Column('x', 'int', True)]), patch('transferdata.erp.engine.runner.Loader') as cls:
        if step == 'execute': source.cursor.return_value.execute.side_effect = RuntimeError('SQL failure')
        else: cls.return_value.write.side_effect = RuntimeError('load failure')
        with pytest.raises(RuntimeError): run(job, Mock())
        cls.return_value.publish.assert_not_called()
        cls.return_value.cleanup.assert_called_once()

@pytest.mark.django_db
def test_connection_failure_redacted(job):
    with patch('transferdata.erp.engine.connections.pyodbc.connect', side_effect=RuntimeError('Password=secret')):
        with pytest.raises(ETLError) as error: check_connection(job.source_connection)
    assert 'secret' not in str(error.value)

@pytest.mark.django_db
@pytest.mark.parametrize('trust', [False, True])
def test_connection_escape_tls_timeout_and_close(job, monkeypatch, trust):
    conf = job.source_connection
    conf.trust_server_certificate = trust
    conf.authentication_type, conf.username, conf.credential_reference = 'SQL', 'reader', 'TEST_ETL_PASSWORD'
    monkeypatch.setenv('TEST_ETL_PASSWORD', 'p};UID=admin')
    with patch('transferdata.erp.engine.connections.pyodbc.connect') as driver:
        with connect(conf, 12) as conn:
            assert conn.timeout == 12
        text = driver.call_args.args[0]
        assert 'PWD={p}};UID=admin}' in text
        assert 'Encrypt={yes}' in text
        assert ('TrustServerCertificate={yes}' if trust else 'TrustServerCertificate={no}') in text
        driver.return_value.close.assert_called_once()

def test_target_lock_contention_never_creates_stage(loader):
    cursor = loader.conn.cursor.return_value
    cursor.fetchone.return_value = (-1,)
    with pytest.raises(ETLError): loader.prepare()
    assert not any(c.args[0].startswith('CREATE') for c in cursor.execute.call_args_list)

def test_published_count_mismatch_rolls_back(loader):
    cursor = loader.conn.cursor.return_value
    cursor.fetchone.side_effect = [(2,), (42,), (0,), (1,)]
    with patch('transferdata.erp.engine.loader.metadata', return_value=loader.columns):
        with pytest.raises(ETLError): loader.publish(2)
    loader.conn.rollback.assert_called_once()
    loader.conn.commit.assert_not_called()
