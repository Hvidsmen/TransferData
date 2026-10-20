"""Conservative SQL Server to PostgreSQL replica table creation."""
from sqlalchemy import (
    BigInteger, Boolean, Column, Date, DateTime, Float, Integer, LargeBinary,
    MetaData, Numeric, SmallInteger, String, Table, Text, Time, Uuid, inspect,
)
from sqlalchemy.dialects import mssql
from sqlalchemy.schema import CreateTable


def postgres_type(source_type):
    # SQL Server TIMESTAMP means rowversion bytes, not a date/time.
    if isinstance(source_type, (mssql.TIMESTAMP, mssql.IMAGE)):
        return LargeBinary()
    if isinstance(source_type, mssql.MONEY):
        return Numeric(19, 4)
    if isinstance(source_type, mssql.SMALLMONEY):
        return Numeric(10, 4)
    if isinstance(source_type, mssql.XML):
        return Text()
    if isinstance(source_type, mssql.DATETIMEOFFSET):
        return DateTime(timezone=True)
    try:
        generic = source_type.as_generic()
    except (NotImplementedError, AssertionError):
        raise ValueError(f"Unsupported source type: {type(source_type).__name__}") from None
    if isinstance(generic, Uuid):
        # ODBC may return UUID strings. String mode accepts those without
        # requiring Python UUID objects; PostgreSQL still stores native UUID.
        return Uuid(as_uuid=False)
    if isinstance(generic, Boolean):
        return Boolean()
    if isinstance(generic, BigInteger):
        return BigInteger()
    if isinstance(generic, SmallInteger):
        return SmallInteger()
    if isinstance(generic, Integer):
        return Integer()  # Includes SQL Server unsigned tinyint (0..255).
    if isinstance(generic, Float):
        return Float(precision=generic.precision)
    if isinstance(generic, Numeric):
        return Numeric(precision=generic.precision, scale=generic.scale)
    if isinstance(generic, String):
        return String(generic.length) if generic.length else Text()
    if isinstance(generic, LargeBinary):
        return LargeBinary()
    if isinstance(generic, DateTime):
        return DateTime(timezone=generic.timezone)
    if isinstance(generic, Date):
        return Date()
    if isinstance(generic, Time):
        return Time(timezone=generic.timezone)
    raise ValueError(f"Unsupported source type: {type(source_type).__name__}")


def prepare_target(source_engine, target_engine, job):
    """Create missing replica table, without reading rows or changing existing tables.

    Source keys, identities, defaults, indexes and computed expressions are
    intentionally not copied: the destination receives explicit source values.
    PostgreSQL DDL runs in its own transaction, separate from the data refresh.
    """
    if target_engine.dialect.name not in {"postgresql", "sqlite"}:
        raise ValueError("Automatic table creation supports PostgreSQL only")
    with source_engine.connect() as source, target_engine.begin() as target:
        schema = job.get("target_schema")
        if target_engine.dialect.name == "postgresql":
            for name in [job["target_table"], schema] if schema else [job["target_table"]]:
                if len(name.encode("utf-8")) > 63:
                    raise ValueError("PostgreSQL identifiers must fit in 63 UTF-8 bytes")
        inspector = inspect(target)
        if inspector.has_table(job["target_table"], schema=schema):
            return False
        source_table = Table(job["source_table"], MetaData(),
                             schema=job.get("source_schema"), autoload_with=source)
        mapping = job.get("columns") or {c.name: c.name for c in source_table.columns}
        if len(set(mapping.values())) != len(mapping):
            raise ValueError("Multiple source columns map to the same target column")
        columns = []
        for source_name, target_name in mapping.items():
            if source_name not in source_table.c:
                raise ValueError(f"Unknown source column: {source_name!r}")
            if target_engine.dialect.name == "postgresql" and len(target_name.encode("utf-8")) > 63:
                raise ValueError("PostgreSQL column names must fit in 63 UTF-8 bytes; use columns mapping")
            column = source_table.c[source_name]
            columns.append(Column(target_name, postgres_type(column.type), nullable=column.nullable))
        replica = Table(job["target_table"], MetaData(), *columns, schema=schema)
        # Compile all types before executing any DDL. Unsupported reflection
        # types fail safely rather than silently becoming arbitrary text.
        CreateTable(replica).compile(dialect=target.dialect)
        replica.create(target)
        return True
