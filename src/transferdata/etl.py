from dataclasses import dataclass
from time import monotonic

from sqlalchemy import MetaData, Table, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from .transfer_errors import TransferDataError, diagnostic, validate_postgres_text


@dataclass(frozen=True)
class TransferResult:
    rows: int
    seconds: float
    cleaned_values: int = 0
    removed_characters: int = 0


def transfer(
    source_engine: Engine,
    target_engine: Engine,
    job: dict,
    *, progress=None,
) -> TransferResult:
    """Replace target rows atomically, streaming source rows in bounded batches.

    The target must already exist. No DDL or TRUNCATE is executed: Oracle DDL
    implicitly commits and would break the rollback guarantee.
    """
    started = monotonic()
    if progress:
        progress(0, "Подключение и проверка структуры")
    remove_nul = job.get("remove_nul_characters", False)
    if type(remove_nul) is not bool:
        raise ValueError("remove_nul_characters must be boolean")
    if remove_nul and target_engine.dialect.name != "postgresql":
        raise ValueError("remove_nul_characters supports PostgreSQL only")
    cleaned_values = removed_characters = 0
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
                if target.dialect.name == "postgresql":
                    cleaned, removed = validate_postgres_text(payload, target_table, copied + 1, remove_nul=remove_nul)
                    cleaned_values += cleaned
                    removed_characters += removed
                try:
                    target.execute(target_table.insert(), payload)
                except DBAPIError as exc:
                    result = diagnostic(exc)
                    if result is None:
                        raise
                    message, details = result
                    column = getattr(getattr(exc.orig, "diag", None), "column_name", None)
                    if column in target_table.c.keys():
                        details["column"] = column
                        message = f"Столбец «{column}»: " + message
                    raise TransferDataError(
                        "Запись пакета в назначение: " + message,
                        **details, batch_first=copied + 1, batch_last=copied + len(batch), phase="target_insert",
                    ) from None
                copied += len(batch)
                if progress:
                    progress(copied, "Запись в транзакции · данные ещё не опубликованы")
                batch = rows.fetchmany(batch_size)
        if progress:
            progress(copied, "Проверка числа строк и публикация")
        actual = target.scalar(select(func.count()).select_from(target_table))
        if actual != copied:
            raise RuntimeError(f"Target count mismatch: expected {copied}, got {actual}")
    return TransferResult(copied, monotonic() - started, cleaned_values, removed_characters)
