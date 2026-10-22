"""Execute a SQL Server procedure via pyodbc without storing its result sets."""
import re


class ProcedureReturnCodeError(RuntimeError):
    def __init__(self, code):
        super().__init__("Procedure returned a nonzero or missing return code")
        self.code = code


def quote_identifier(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 128 or any(ord(c) < 32 for c in value):
        raise ValueError("Invalid procedure identifier")
    return "[" + value.replace("]", "]]") + "]"


def execute_procedure(engine, job):
    if engine.dialect.name != "mssql" or engine.dialect.driver != "pyodbc":
        raise ValueError("Procedure jobs require SQL Server with Windows ODBC authentication")
    parameters = job.get("procedure_parameters") or {}
    if not isinstance(parameters, dict) or not all(
        isinstance(name, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,126}", name)
        for name in parameters
    ):
        raise ValueError("Invalid procedure parameter names")
    name = quote_identifier(job["procedure_schema"]) + "." + quote_identifier(job["procedure_name"])
    arguments = ", ".join(f"@{key}=?" for key in parameters)
    sql = (f"DECLARE @__etl_return int; EXEC @__etl_return = {name} {arguments}; "
           "SELECT @__etl_return AS [__etl_return_code];")
    connection = engine.raw_connection()
    cursor = None
    try:
        connection.driver_connection.timeout = job.get("procedure_timeout", 3600)
        cursor = connection.cursor()
        cursor.execute(sql, tuple(parameters.values()))
        return_code = None
        while True:
            if cursor.description:
                if [c[0] for c in cursor.description] == ["__etl_return_code"]:
                    return_code = cursor.fetchone()[0]
                else:
                    while cursor.fetchmany(1000):
                        pass
            if not cursor.nextset():
                break
        if return_code != 0:
            raise ProcedureReturnCodeError(return_code)
        connection.commit()
        return return_code
    except Exception:
        connection.rollback()
        raise
    finally:
        if cursor is not None:
            cursor.close()
        connection.close()
