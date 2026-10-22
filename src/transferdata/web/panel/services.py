from time import monotonic
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from django.db import IntegrityError, transaction
from django.utils import timezone
from sqlalchemy import inspect, text

from transferdata.cli import engines
from transferdata.etl import transfer
from transferdata.schema import prepare_target
from .models import Job, Run
from transferdata.web.maintenance import is_paused
from django.core.exceptions import ValidationError


def enqueue(job, action):
    if is_paused():
        raise ValidationError("Портал обновляется. Повторите запуск после завершения обновления.")
    if action not in dict(Run.ACTIONS):
        raise ValueError("Unknown action")
    job.full_clean()
    try:
        with transaction.atomic():
            return Run.objects.create(job=job, action=action, snapshot=job.config()), True
    except IntegrityError:
        active = Run.objects.filter(job=job, status__in=["queued", "running"]).first()
        if active is None:
            raise
        return active, False


def schedule_due(now=None):
    now = now or timezone.now()
    for job in Job.objects.filter(enabled=True).select_related("connection"):
        trigger = CronTrigger.from_crontab(job.cron, timezone=ZoneInfo(job.timezone))
        if job.next_run_at is None:
            Job.objects.filter(pk=job.pk, next_run_at=None).update(
                next_run_at=trigger.get_next_fire_time(None, now))
        elif job.next_run_at <= now:
            with transaction.atomic():
                enqueue(job, "run")
                Job.objects.filter(pk=job.pk).update(next_run_at=trigger.get_next_fire_time(now, now))


def execute_next():
    run = Run.objects.filter(status="queued").order_by("created_at", "pk").first()
    if run is None:
        return False
    if not Run.objects.filter(pk=run.pk, status="queued").update(status="running", started_at=timezone.now()):
        return False
    started = monotonic()
    source = target = None
    try:
        config = run.snapshot
        job = config["jobs"][0]
        source, target = engines(config)
        if run.action == "check":
            for engine in (source, target):
                with engine.connect() as connection:
                    if connection.scalar(text("SELECT 1")) != 1:
                        raise RuntimeError("Unexpected database response")
            run.message = "Оба подключения проверены."
        elif run.action == "inspect":
            with source.connect() as connection:
                inspector = inspect(connection)
                columns = inspector.get_columns(job["source_table"], schema=job["source_schema"])
                run.details = {"tables": inspector.get_table_names(schema=job["source_schema"]),
                               "columns": [{"name": c["name"], "type": str(c["type"]),
                                            "bytes": len(c["name"].encode("utf-8"))} for c in columns]}
            run.message = "Структура получена. Имена длиннее 63 UTF-8 байт нужно переименовать в маппинге."
        elif run.action == "prepare":
            created = prepare_target(source, target, job)
            run.message = "Таблица создана." if created else "Таблица уже существует, изменений нет."
        elif run.action == "run":
            result = transfer(source, target, job)
            run.rows = result.rows
            run.message = "Полная загрузка завершена."
        else:
            raise ValueError("Unknown action")
        run.status = "success"
    except Exception as exc:
        # Driver exception text can contain row values, SQL and credentials.
        # Never persist it or print an exception traceback into the panel.
        run.status = "failed"
        run.message = f"Ошибка: {type(exc).__name__}. Проверьте подключения, права, имена и типы столбцов."
    finally:
        for engine in (source, target):
            if engine is not None:
                engine.dispose()
    run.seconds = round(monotonic() - started, 3)
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "rows", "seconds", "message", "details", "finished_at"])
    return True
