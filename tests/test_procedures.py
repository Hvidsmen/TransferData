from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from transferdata.procedures import execute_procedure, quote_identifier


def engine_with_cursor():
    engine = MagicMock()
    engine.dialect = SimpleNamespace(name="mssql", driver="pyodbc")
    cursor = engine.raw_connection.return_value.cursor.return_value
    cursor.description = [("__etl_return_code",)]
    cursor.fetchone.return_value = (0,)
    cursor.nextset.return_value = False
    return engine, cursor


def test_named_parameters_are_bound_and_identifier_is_quoted():
    engine, cursor = engine_with_cursor()
    job = {"procedure_schema": "dbo", "procedure_name": "Refresh]; DROP TABLE x;--",
           "procedure_parameters": {"DateFrom": "'; DROP TABLE x;--", "Flag": True},
           "procedure_timeout": 120}
    assert execute_procedure(engine, job) == 0
    sql, values = cursor.execute.call_args.args
    assert "[dbo].[Refresh]]; DROP TABLE x;--]" in sql
    assert "@DateFrom=?, @Flag=?" in sql
    assert "'; DROP TABLE" not in sql
    assert values == ("'; DROP TABLE x;--", True)
    engine.raw_connection.return_value.commit.assert_called_once()
    engine.raw_connection.return_value.rollback.assert_not_called()
    assert engine.raw_connection.return_value.driver_connection.timeout == 120
    cursor.close.assert_called_once()


def test_results_are_drained_before_commit():
    engine, cursor = engine_with_cursor()
    cursor.description = [("data",)]
    cursor.fetchmany.side_effect = [[("one",)], []]
    def nextset():
        cursor.description = [("__etl_return_code",)]
        return cursor.nextset.call_count == 1
    cursor.nextset.side_effect = nextset
    execute_procedure(engine, {"procedure_schema": "dbo", "procedure_name": "Refresh"})
    assert cursor.fetchmany.call_count == 2
    assert cursor.nextset.call_count == 2


@pytest.mark.parametrize("failure", ["nonzero", "execute", "nextset"])
def test_error_rolls_back_and_does_not_retry(failure):
    engine, cursor = engine_with_cursor()
    if failure == "nonzero":
        cursor.fetchone.return_value = (17,)
    else:
        getattr(cursor, failure).side_effect = RuntimeError("driver detail")
    with pytest.raises(RuntimeError):
        execute_procedure(engine, {"procedure_schema": "dbo", "procedure_name": "Refresh"})
    engine.raw_connection.return_value.rollback.assert_called_once()
    engine.raw_connection.return_value.commit.assert_not_called()
    cursor.close.assert_called_once()
    engine.raw_connection.return_value.close.assert_called_once()
    assert cursor.execute.call_count == 1


def test_parameter_name_cannot_inject_sql():
    engine, _ = engine_with_cursor()
    with pytest.raises(ValueError):
        execute_procedure(engine, {"procedure_schema": "dbo", "procedure_name": "Refresh",
                                   "procedure_parameters": {"a=1; DROP TABLE x--": 1}})
    engine.raw_connection.assert_not_called()
    assert quote_identifier("РусскоеИмя") == "[РусскоеИмя]"
