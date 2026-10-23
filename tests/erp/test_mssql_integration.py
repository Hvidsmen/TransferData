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


@pytest.mark.skipif(os.environ.get('ETL_RUN_MSSQL_TESTS') != '1', reason='Real SQL Server not configured')
def test_reference_select_preserves_rows_and_decodes_references_without_source_writes():
    from transferdata.erp.control.onec import mapping_from_metadata, source_query
    config = DatabaseConnection(name='Reference integration', server=os.environ['MSSQL_TEST_SERVER'],
        port=int(os.environ.get('MSSQL_TEST_PORT', '1433')), database=os.environ['MSSQL_TEST_DATABASE'],
        authentication_type='SQL', username=os.environ['MSSQL_TEST_USERNAME'],
        credential_reference='MSSQL_TEST_PASSWORD', role='SOURCE')
    data = {'reference_version': 1, 'table': '_Rows', 'properties': [
        {'name': 'id', 'columns': [{'name': 'id', 'purpose': 'Value'}]},
        {'name': 'Инвойс', 'is_reference': True, 'references': ['Документ.Инвойс'], 'columns': [
            {'name': '_Ref', 'purpose': 'Value'}]},
    ], 'reference_targets': [{'name': 'Документ.Инвойс', 'kind': 'Документ', 'type_code': 42,
        'table': '_Documents', 'id': '_IDRRef', 'date': '_Date', 'number': '_Number'}]}
    query = source_query(mapping_from_metadata(data), adjust_dates=True, resolve_references=True).replace('[dbo].', '')
    ctes = """WITH [_Rows] AS (
        SELECT * FROM (VALUES
            (1, CONVERT(binary(16), 0x01)),
            (2, CONVERT(binary(16), 0x02)),
            (3, CONVERT(binary(16), 0x00)),
            (4, CONVERT(binary(16), NULL))) AS v(id, _Ref)
        ), [_Documents] AS (
            SELECT CONVERT(binary(16), 0x01) AS _IDRRef,
                   CONVERT(datetime2, '4026-10-23T12:30:00') AS _Date, N'INV-1' AS _Number
        ) """
    with connect(config) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(ctes + query)
            names = [column[0] for column in cursor.description]
            rows = [dict(zip(names, row)) for row in cursor.fetchall()]
            assert len(rows) == 4
            by_id = {row['id']: row for row in rows}
            assert by_id[1]['ИнвойсНомер'] == 'INV-1'
            assert by_id[1]['ИнвойсДата'].year == 2026
            assert 'INV-1' in by_id[1]['Инвойс']
            assert by_id[1]['ИнвойсСсылка'] == bytes(15) + b'\x01'
            assert all(by_id[n]['Инвойс'] is None for n in (2, 3, 4))
            assert all(by_id[n]['ИнвойсДата'] is None for n in (2, 3, 4))
        finally:
            cursor.close()


@pytest.mark.skipif(os.environ.get('ETL_RUN_MSSQL_TESTS') != '1', reason='Real SQL Server not configured')
def test_payment_allocation_preserves_positive_negative_and_fallback_totals():
    from decimal import Decimal
    from transferdata.erp.reports.payment_schedule_items import allocated_amount
    config = DatabaseConnection(name='Allocation integration', server=os.environ['MSSQL_TEST_SERVER'],
        port=int(os.environ.get('MSSQL_TEST_PORT', '1433')), database=os.environ['MSSQL_TEST_DATABASE'],
        authentication_type='SQL', username=os.environ['MSSQL_TEST_USERNAME'],
        credential_reference='MSSQL_TEST_PASSWORD', role='SOURCE')
    # Read-only SQL exercises SQL Server decimal precision and cumulative rounding,
    # including three equal goods lines, a credit payment and an unallocated zero-total order.
    sql = f'''WITH [payments] AS (
      SELECT * FROM (VALUES (1, CAST(100 AS decimal(28,6))), (2, -CAST(100 AS decimal(28,6))),
      (3, CAST(0.01 AS decimal(28,6))), (4, CAST(12.345678 AS decimal(28,6)))) AS v([id], [USD])
    ), [shares] AS (
      SELECT *, CAST([last] AS decimal(28,6)) / CAST(3 AS decimal(28,6)) AS [ThroughShare],
      CAST([first] AS decimal(28,6)) / CAST(3 AS decimal(28,6)) AS [BeforeShare]
      FROM (VALUES (1, 0, 1), (2, 1, 2), (3, 2, 3)) AS v([line], [first], [last])
    ) SELECT [p].[id], SUM({allocated_amount('USD')})
    FROM [payments] AS [p]
    CROSS APPLY (SELECT CAST(3 AS decimal(28,6)) AS [OrderTotal], [ThroughShare], [BeforeShare]
      FROM [shares] WHERE [p].[id] <> 4
      UNION ALL SELECT 0, NULL, NULL WHERE [p].[id] = 4) AS [i]
    GROUP BY [p].[id] ORDER BY [p].[id]'''
    with connect(config) as conn:
        cursor = conn.cursor()
        try:
            actual = [row[1] for row in cursor.execute(sql).fetchall()]
        finally:
            cursor.close()
    assert actual == [Decimal('100'), Decimal('-100'), Decimal('0.01'), Decimal('12.345678')]
