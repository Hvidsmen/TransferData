from dataclasses import dataclass
from time import monotonic

from sqlalchemy import MetaData, Table, func, select
from sqlalchemy.engine import Engine


@dataclass(frozen=True)
class TransferResult:
    rows: int
    seconds: float


def transfer(
    source_engine: Engine,
    target_engine: Engine,
    job: dict,
) -> TransferResult:
    """Replace target rows atomically, streaming source rows in bounded batches.

    The target must already exist. No DDL or TRUNCATE is executed: Oracle DDL
    implicitly commits and would break the rollback guarantee.
    """
    started = monotonic()
    batch_size = job.get("batch_size", 1000)
    if type(batch_size) is not int or batch_size < 1:
        raise ValueError("batch_size must be a positive integer")

    with source_engine.connect() as source, target_engine.begin() as target:
        source_table = Table(
            job["source_table"], MetaData(),
            schema=job.get("source_schema"), autoload_with=source,
        )
        target_table = Table(
            job["target_table"], MetaData(),
            schema=job.get("target_schema"), autoload_with=target,
        )
        # Optional explicit mapping supports Cyrillic source / Latin target names.
        mapping = job.get("columns") or {c.name: c.name for c in source_table.columns}
        if len(set(mapping.values())) != len(mapping):
            raise ValueError("Multiple source columns map to the same target column")
        for source_name, target_name in mapping.items():
            if source_name not in source_table.c or target_name not in target_table.c:
                raise ValueError(f"Unknown mapped column: {source_name!r} -> {target_name!r}")

        # Prevent concurrent writers during refresh in Oracle. Readers continue
        # seeing committed data through Oracle's read consistency.
        if target.dialect.name in {"oracle", "postgresql"}:
            qualified_name = target.dialect.identifier_preparer.format_table(target_table)
            target.exec_driver_sql(f"LOCK TABLE {qualified_name} IN EXCLUSIVE MODE NOWAIT")

        statement = select(*(source_table.c[name] for name in mapping))
        with source.execution_options(stream_results=True).execute(statement) as rows:
            batch = rows.fetchmany(batch_size)
            if not batch and not job.get("allow_empty", False):
                raise ValueError("Source is empty; target preserved (allow_empty=false)")
            target.execute(target_table.delete())
            copied = 0
            while batch:
                payload = [dict(zip(mapping.values(), row)) for row in batch]
                target.execute(target_table.insert(), payload)
                copied += len(batch)
                batch = rows.fetchmany(batch_size)
        actual = target.scalar(select(func.count()).select_from(target_table))
        if actual != copied:
            raise RuntimeError(f"Target count mismatch: expected {copied}, got {actual}")
    return TransferResult(copied, monotonic() - started)
