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
