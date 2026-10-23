import pytest
from threading import Event
from datetime import datetime, timezone
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, select

from transferdata.etl import transfer
from transferdata.cli import validate, instance_lock, run_job


@pytest.fixture
def tables(tmp_path):
    src = create_engine(f"sqlite:///{tmp_path}/source.db")
    dst = create_engine(f"sqlite:///{tmp_path}/target.db")
    a = Table("Справочники.Контрагенты", MetaData(), Column("id", Integer, primary_key=True), Column("name", String))
    b = Table("CONTRACTORS", MetaData(), Column("id", Integer, primary_key=True), Column("name", String, nullable=False))
    a.create(src)
    b.create(dst)
    with dst.begin() as conn:
        conn.execute(b.insert(), {"id": 99, "name": "old"})
    yield src, dst, a, b
    src.dispose()
    dst.dispose()


def job(**kwargs):
    return {"source_table": "Справочники.Контрагенты", "target_table": "CONTRACTORS", "batch_size": 1, **kwargs}


def contents(engine, table):
    with engine.connect() as conn:
        return conn.execute(select(table).order_by(table.c.id)).all()


def test_refresh_streams_unicode_and_replaces_rows(tables):
    src, dst, a, b = tables
    with src.begin() as conn:
        conn.execute(a.insert(), [{"id": 1, "name": "ООО Пример"}, {"id": 2, "name": "ИП Тест"}])
    result = transfer(src, dst, job())
    assert result.rows == 2
    assert contents(dst, b) == [(1, "ООО Пример"), (2, "ИП Тест")]
    assert transfer(src, dst, job()).rows == 2
    assert len(contents(dst, b)) == 2


def test_failure_after_first_batch_rolls_back_delete_and_inserts(tables):
    src, dst, a, b = tables
    with src.begin() as conn:
        conn.execute(a.insert(), [{"id": 1, "name": "valid"}, {"id": 2, "name": None}])
    with pytest.raises(Exception):
        transfer(src, dst, job())
    assert contents(dst, b) == [(99, "old")]


def test_empty_source_preserves_target(tables):
    src, dst, _, b = tables
    with pytest.raises(ValueError, match="Source is empty"):
        transfer(src, dst, job())
    assert contents(dst, b) == [(99, "old")]


def test_allow_empty_clears_target(tables):
    src, dst, _, b = tables
    assert transfer(src, dst, job(allow_empty=True)).rows == 0
    assert contents(dst, b) == []


def test_missing_column_preserves_target(tables):
    src, dst, _, b = tables
    with pytest.raises(ValueError, match="Unknown mapped column"):
        transfer(src, dst, job(columns={"id": "missing"}))
    assert contents(dst, b) == [(99, "old")]


def test_column_mapping(tables):
    src, dst, a, b = tables
    with src.begin() as conn:
        conn.execute(a.insert(), {"id": 1, "name": "Тест"})
    assert transfer(src, dst, job(columns={"id": "id", "name": "name"})).rows == 1
    assert contents(dst, b) == [(1, "Тест")]


def test_duplicate_jobs_rejected():
    item = {"name": "x", "source_table": "a", "target_table": "b", "cron": "0 3 * * *"}
    with pytest.raises(ValueError, match="unique"):
        validate({"target": {}, "jobs": [item, item]})


def test_exclusive_service_lock(tmp_path):
    with instance_lock(tmp_path):
        with pytest.raises(RuntimeError, match="Another instance"):
            with instance_lock(tmp_path):
                pass
    with instance_lock(tmp_path):
        pass


def test_scheduler_runs_real_transfer(tables):
    src, dst, a, b = tables
    with src.begin() as conn:
        conn.execute(a.insert(), {"id": 1, "name": "По расписанию"})
    finished = Event()
    outcomes = []
    scheduler = BackgroundScheduler(timezone="Europe/Moscow")

    def on_finished(event):
        outcomes.append(event)
        finished.set()

    scheduler.add_listener(on_finished, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)
    scheduler.add_job(run_job, "date", run_date=datetime.now(timezone.utc),
                      args=[src, dst, job(name="scheduled")])
    scheduler.start()
    try:
        assert finished.wait(5), "Scheduler did not execute the transfer"
        assert outcomes[0].exception is None
        assert contents(dst, b) == [(1, "По расписанию")]
    finally:
        scheduler.shutdown(wait=True)


@pytest.mark.parametrize("trust", [False, True])
@pytest.mark.parametrize("sslmode", [None, "require"])
def test_windows_auth_does_not_request_sql_password(monkeypatch, trust, sslmode):
    from unittest.mock import MagicMock
    from transferdata import cli
    created = []
    requested = []

    def fake_create(url, **kwargs):
        created.append(url)
        return MagicMock()

    def fake_env(name):
        requested.append(name)
        return "test-only-value"

    monkeypatch.setattr(cli, "create_engine", fake_create)
    monkeypatch.setattr(cli, "required_env", fake_env)
    target = {"dialect": "postgresql", "host": "postgres.test", "database": "analytics"}
    if sslmode:
        target["sslmode"] = sslmode
    cli.engines({
        "source": {"auth": "windows", "host": "vm-dwh", "database": "DataWH",
                   "trust_server_certificate": trust},
        "target": target,
    })
    assert created[0].drivername == "mssql+pyodbc"
    assert created[0].username is None
    assert created[0].password is None
    assert created[0].query["Trusted_Connection"] == "yes"
    assert created[0].query["TrustServerCertificate"] == ("yes" if trust else "no")
    assert created[0].query["Encrypt"] == "yes"
    assert requested == ["TARGET_USER", "TARGET_PASSWORD"]
    assert created[1].query["sslmode"] == (sslmode or "verify-full")


def test_unencrypted_postgres_mode_is_rejected():
    with pytest.raises(ValueError, match="target.sslmode"):
        validate({"target": {"sslmode": "disable"}})


def test_diagnostic_failure_after_batch_preserves_old_target(tables):
    from sqlalchemy import event
    from sqlalchemy.exc import DataError
    from transferdata.transfer_errors import TransferDataError
    src, dst, a, b = tables
    with src.begin() as conn:
        conn.execute(a.insert(), [{'id': 1, 'name': 'valid'}, {'id': 2, 'name': 'private-value'}])
    class DriverError(Exception):
        sqlstate = '22003'
    def fail_second_insert(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith('INSERT') and parameters[0] == 2:
            raise DataError(statement, parameters, DriverError('private-value password=secret'))
    event.listen(dst, 'before_cursor_execute', fail_second_insert)
    with pytest.raises(TransferDataError) as caught:
        transfer(src, dst, job())
    assert caught.value.details['batch_first'] == 2 and caught.value.details['batch_last'] == 2
    assert '22003' in str(caught.value) and 'secret' not in str(caught.value)
    assert contents(dst, b) == [(99, 'old')]


def test_progress_counts_do_not_publish_uncommitted_data(tables):
    src, dst, source, target = tables
    with src.begin() as conn:
        conn.execute(source.insert(), [{"id": 1, "name": "First"}, {"id": 2, "name": "Second"}])
    observations = []
    def progress(count, phase):
        observations.append((count, phase))
        assert contents(dst, target) == [(99, "old")]
    result = transfer(src, dst, job(), progress=progress)
    assert result.rows == 2
    assert [count for count, _ in observations] == [0, 1, 2, 2]
    assert contents(dst, target) == [(1, "First"), (2, "Second")]
