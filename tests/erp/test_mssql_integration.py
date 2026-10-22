"""Opt-in real SQL Server test. Uses an isolated uniquely named test table only."""
import os
import uuid
from decimal import Decimal
import pytest
from transferdata.erp.control.models import DatabaseConnection
from transferdata.erp.engine.connections import connect
from transferdata.erp.engine.extractor import metadata
from transferdata.erp.engine.loader import Loader
from transferdata.erp.engine.transformer import Column

pytestmark = [pytest.mark.mssql, pytest.mark.django_db]

@pytest.mark.skipif(os.environ.get('ETL_RUN_MSSQL_TESTS') != '1', reason='Real SQL Server not configured; set ETL_RUN_MSSQL_TESTS=1 and MSSQL_TEST_*')
def test_real_full_load_and_rollback():
    config = DatabaseConnection(name='Integration', server=os.environ['MSSQL_TEST_SERVER'], port=int(os.environ.get('MSSQL_TEST_PORT', '1433')), database=os.environ['MSSQL_TEST_DATABASE'], authentication_type='SQL', username=os.environ['MSSQL_TEST_USERNAME'], credential_reference='MSSQL_TEST_PASSWORD', role='TARGET')
    table = 'test_etl_' + uuid.uuid4().hex
    columns = [Column('id', 'int', False), Column('amount', 'decimal(38,18)', True), Column('ref', 'varbinary(16)', True), Column('title', 'nvarchar(100)', True)]
    rows = [(1, Decimal('12345678901234567890.123456789012345678'), b'\x00\xff', 'Проверка'), (2, None, None, None)]
    with connect(config) as conn:
        cursor = conn.cursor()
        try:
            loader = Loader(conn, 'dbo', table, columns)
            try:
                loader.prepare()
                loader.write(rows)
                loader.publish(2)
            finally:
                loader.cleanup()
            assert metadata(conn, f'SELECT * FROM [dbo].[{table}]') == columns
            actual = cursor.execute(f'SELECT * FROM [dbo].[{table}] ORDER BY id').fetchall()
            assert [tuple(row) for row in actual] == rows
            # Existing target: force a SQL Server INSERT error after DELETE.
            cursor.execute(f'ALTER TABLE [dbo].[{table}] ADD CONSTRAINT [ck_{table}] CHECK (id < 3)')
            conn.commit()
            loader = Loader(conn, 'dbo', table, columns)
            try:
                loader.prepare()
                loader.write([(3, None, None, None)])
                with pytest.raises(Exception): loader.publish(1)
                actual = cursor.execute(f'SELECT * FROM [dbo].[{table}] ORDER BY id').fetchall()
                assert [tuple(row) for row in actual] == rows
            finally:
                loader.cleanup()
        finally:
            cursor.execute(f'DROP TABLE IF EXISTS [dbo].[{table}]')
            conn.commit()
            cursor.close()


@pytest.mark.skipif(os.environ.get('ETL_RUN_MSSQL_TESTS') != '1', reason='Real SQL Server not configured')
def test_onec_date_rule_boundaries_without_changing_database():
    from datetime import datetime
    from transferdata.erp.control.onec import source_query
    config = DatabaseConnection(name='Date integration', server=os.environ['MSSQL_TEST_SERVER'],
        port=int(os.environ.get('MSSQL_TEST_PORT', '1433')), database=os.environ['MSSQL_TEST_DATABASE'],
        authentication_type='SQL', username=os.environ['MSSQL_TEST_USERNAME'], credential_reference='MSSQL_TEST_PASSWORD')
    query = source_query({'schema': 'dbo', 'table': 'sample', 'columns': [
        {'source': '_Date_Time', 'alias': 'Дата', 'is_date': True}]}, adjust_dates=True)
    values = [datetime(1753, 1, 1), datetime(2026, 10, 22), datetime(3752, 1, 1),
              datetime(3753, 1, 1), datetime(4026, 10, 22), None]
    rows = ','.join('(?, CAST(? AS datetime2(0)))' for _ in values)
    query = query.replace('FROM [dbo].[sample]', f'FROM (VALUES {rows}) AS sample([position], [_Date_Time]) ORDER BY [position]')
    parameters = [item for index, value in enumerate(values) for item in (index, value)]
    with connect(config) as conn:
        cursor = conn.cursor()
        try:
            actual = [row[0] for row in cursor.execute(query, parameters).fetchall()]
        finally:
            cursor.close()
    assert actual == [values[0], values[1], values[2], datetime(1753, 1, 1), datetime(2026, 10, 22), None]
