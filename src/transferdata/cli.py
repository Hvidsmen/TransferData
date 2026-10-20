import argparse
from contextlib import contextmanager
import portalocker
import json
import logging
import os
from pathlib import Path
import signal
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import URL, create_engine, text

from transferdata.etl import transfer
from transferdata.schema import prepare_target

log = logging.getLogger("transferdata")


def required_env(name):
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"Required environment variable is missing: {name}")
    return value


def engines(config):
    source, target = config["source"], config["target"]
    if source.get("auth") == "windows":
        source_url = URL.create(
            "mssql+pyodbc", host=source["host"], database=source["database"],
            query={"driver": source.get("driver", "ODBC Driver 18 for SQL Server"),
                   "Trusted_Connection": "yes", "Encrypt": "yes",
                   "TrustServerCertificate": "yes" if source.get("trust_server_certificate", False) else "no"},
        )
        src = create_engine(source_url, pool_pre_ping=True, hide_parameters=True,
                            connect_args={"timeout": 30})
    else:
        src = create_engine(URL.create(
        "mssql+pymssql", host=source["host"], port=source.get("port", 1433),
        database=source["database"],
        username=required_env(source.get("username_env", "SQLSERVER_USER")),
        password=required_env(source.get("password_env", "SQLSERVER_PASSWORD")),
        query={"charset": "utf8"},
    ), pool_pre_ping=True, hide_parameters=True,
        connect_args={"login_timeout": 30, "timeout": 300, "encryption": "require"})
    try:
        if target.get("dialect", "oracle") == "postgresql":
            target_url = URL.create(
                "postgresql+psycopg", host=target["host"], port=target.get("port", 5432),
                database=target["database"],
                username=required_env(target.get("username_env", "TARGET_USER")),
                password=required_env(target.get("password_env", "TARGET_PASSWORD")),
                query={"sslmode": "verify-full", "connect_timeout": "30"},
            )
        else:
            target_url = URL.create(
                "oracle+oracledb", host=target["host"], port=target.get("port", 1521),
                username=required_env(target.get("username_env", "ORACLE_USER")),
                password=required_env(target.get("password_env", "ORACLE_PASSWORD")),
                query={"service_name": target["service_name"]},
            )
        dst = create_engine(target_url, pool_pre_ping=True, hide_parameters=True)
    except Exception:
        src.dispose()
        raise
    return src, dst


def validate(config):
    if type(config.get("source", {}).get("trust_server_certificate", False)) is not bool:
        raise ValueError("source.trust_server_certificate must be boolean")
    if config["target"].get("dialect", "oracle") not in {"oracle", "postgresql"}:
        raise ValueError("Target dialect must be oracle or postgresql")
    timezone = ZoneInfo(config.get("timezone", "Europe/Moscow"))
    jobs = config["jobs"]
    if not jobs:
        raise ValueError("At least one job is required")
    names = set()
    for job in jobs:
        for key in ("name", "source_table", "target_table", "cron"):
            if not isinstance(job.get(key), str) or not job[key]:
                raise ValueError(f"Job requires a nonempty {key}")
        if job["name"] in names:
            raise ValueError("Job names must be unique")
        names.add(job["name"])
        if type(job.get("batch_size", 1000)) is not int or job.get("batch_size", 1000) < 1:
            raise ValueError("batch_size must be a positive integer")
        if type(job.get("allow_empty", False)) is not bool:
            raise ValueError("allow_empty must be boolean")
        if "columns" in job and (
            not isinstance(job["columns"], dict) or not job["columns"]
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in job["columns"].items())
        ):
            raise ValueError("columns must be a nonempty source-to-target mapping")
        CronTrigger.from_crontab(job["cron"], timezone=timezone)
    return timezone


@contextmanager
def instance_lock(directory):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "service.lock").open("a") as handle:
        try:
            portalocker.lock(handle, portalocker.LOCK_EX | portalocker.LOCK_NB)
        except portalocker.exceptions.LockException:
            raise RuntimeError("Another instance uses this lock directory") from None
        try:
            yield
        finally:
            portalocker.unlock(handle)


def run_job(source, target, job):
    log.info("job=%s status=started", job["name"])
    try:
        result = transfer(source, target, job)
    except Exception as exc:
        # Driver errors may include sensitive row data or SQL parameters. Log
        # only the exception class; never serialize driver exceptions here.
        log.error("job=%s status=failed error_type=%s", job["name"], type(exc).__name__)
        raise RuntimeError(f"Job {job['name']} failed; exception type: {type(exc).__name__}") from None
    log.info("job=%s status=success rows=%d seconds=%.3f", job["name"], result.rows, result.seconds)
    return result


def main():
    parser = argparse.ArgumentParser(description="Scheduled SQL Server ETL to PostgreSQL or Oracle")
    parser.add_argument("command", choices=["validate", "check", "prepare", "run", "serve"])
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--job", help="Run only the named job (run command)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        config_path = Path(args.config).resolve()
        config = json.loads(config_path.read_text(encoding="utf-8"))
        timezone = validate(config)
        if args.command == "validate":
            log.info("Configuration valid; jobs=%d (database connections not checked)", len(config["jobs"]))
            return
        if args.job and args.command not in {"run", "prepare"}:
            raise ValueError("--job is supported only for run or prepare")
        jobs = [job for job in config["jobs"] if not args.job or job["name"] == args.job]
        if not jobs:
            raise ValueError("Unknown job name")
        lock_dir = Path(config.get("lock_directory", ".state"))
        if not lock_dir.is_absolute():
            lock_dir = config_path.parent / lock_dir
        with instance_lock(lock_dir):
            source, target = engines(config)
            try:
                if args.command == "check":
                    for name, engine in [("source", source), ("target", target)]:
                        try:
                            with engine.connect() as connection:
                                query = "SELECT 1 FROM DUAL" if engine.dialect.name == "oracle" else "SELECT 1"
                                if connection.scalar(text(query)) != 1:
                                    raise RuntimeError("Unexpected database response")
                            log.info("%s connection verified", name)
                        except Exception as exc:
                            raise RuntimeError(f"{name} connection failed: {type(exc).__name__}") from None
                elif args.command == "prepare":
                    for job in jobs:
                        try:
                            created = prepare_target(source, target, job)
                        except ValueError:
                            raise
                        except Exception as exc:
                            raise RuntimeError(f"Table preparation failed: {type(exc).__name__}") from None
                        log.info("job=%s table=%s", job["name"], "created" if created else "already_exists")
                elif args.command == "run":
                    for job in jobs:
                        run_job(source, target, job)
                else:
                    # One worker also serializes distinct jobs sharing a target.
                    scheduler = BlockingScheduler(timezone=timezone,
                        executors={"default": {"type": "threadpool", "max_workers": 1}})
                    for job in jobs:
                        scheduler.add_job(run_job,
                            CronTrigger.from_crontab(job["cron"], timezone=timezone),
                            args=[source, target, job], id=job["name"],
                            max_instances=1, coalesce=True, misfire_grace_time=3600)
                    def stop(signum, frame):
                        scheduler.shutdown(wait=True)
                    signal.signal(signal.SIGTERM, stop)
                    signal.signal(signal.SIGINT, stop)
                    log.info("Scheduler started; jobs=%d timezone=%s", len(jobs), timezone)
                    scheduler.start()
            finally:
                source.dispose()
                target.dispose()
    except (Exception, KeyboardInterrupt) as exc:
        # Config validation errors are safe; driver exceptions are sanitized by run_job.
        if isinstance(exc, (ValueError, KeyError, RuntimeError, FileNotFoundError)):
            log.error("%s", exc)
        else:
            log.error("Service failed: %s", type(exc).__name__)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
