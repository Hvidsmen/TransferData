import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, inspect, select
from sqlalchemy.dialects import mssql, postgresql
from sqlalchemy.types import NullType

from transferdata.etl import transfer
from transferdata.schema import postgres_type, prepare_target


@pytest.mark.parametrize("source_type, expected", [
    (mssql.TIMESTAMP(), "BYTEA"),
    (mssql.IMAGE(), "BYTEA"),
    (mssql.VARBINARY(None), "BYTEA"),
    (mssql.UNIQUEIDENTIFIER(), "UUID"),
    (mssql.NVARCHAR(200), "VARCHAR(200)"),
    (mssql.NVARCHAR(None), "TEXT"),
    (mssql.BIT(), "BOOLEAN"),
    (mssql.TINYINT(), "INTEGER"),
    (mssql.BIGINT(), "BIGINT"),
    (mssql.DECIMAL(18, 4), "NUMERIC(18, 4)"),
    (mssql.MONEY(), "NUMERIC(19, 4)"),
    (mssql.SMALLMONEY(), "NUMERIC(10, 4)"),
    (mssql.XML(), "TEXT"),
    (mssql.DATETIMEOFFSET(), "TIMESTAMP WITH TIME ZONE"),
    (mssql.DATETIME2(), "TIMESTAMP WITHOUT TIME ZONE"),
    (mssql.DATE(), "DATE"),
    (mssql.TIME(), "TIME WITHOUT TIME ZONE"),
])
def test_sql_server_types_compile_for_postgres(source_type, expected):
    assert str(postgres_type(source_type).compile(dialect=postgresql.dialect())) == expected


def test_unknown_type_is_rejected():
    with pytest.raises(ValueError, match="Unsupported source type"):
        postgres_type(NullType())


def test_create_then_load_and_prepare_again_preserves_data(tmp_path):
    src = create_engine(f"sqlite:///{tmp_path}/source.db")
    dst = create_engine(f"sqlite:///{tmp_path}/target.db")
    source = Table("Справочники.Контрагенты", MetaData(),
                   Column("Код", Integer, primary_key=True), Column("Имя", String(50)))
    source.create(src)
    with src.begin() as conn:
        conn.execute(source.insert(), {"Код": 7, "Имя": "Пример"})
    job = {"source_table": source.name, "target_table": "contractors",
           "columns": {"Код": "code", "Имя": "name"}}
    try:
        assert prepare_target(src, dst, job) is True
        target = Table("contractors", MetaData(), autoload_with=dst)
        assert not list(target.primary_key)  # Identity/keys are not replicated.
        assert transfer(src, dst, job).rows == 1
        assert prepare_target(src, dst, job) is False
        with dst.connect() as conn:
            assert conn.execute(select(target)).all() == [(7, "Пример")]
    finally:
        src.dispose()
        dst.dispose()


def test_bad_mapping_does_not_create_table(tmp_path):
    src = create_engine(f"sqlite:///{tmp_path}/source.db")
    dst = create_engine(f"sqlite:///{tmp_path}/target.db")
    source = Table("source", MetaData(), Column("id", Integer))
    source.create(src)
    try:
        with pytest.raises(ValueError, match="Unknown source column"):
            prepare_target(src, dst, {"source_table": "source", "target_table": "replica",
                                      "columns": {"missing": "id"}})
        assert not inspect(dst).has_table("replica")
    finally:
        src.dispose()
        dst.dispose()
