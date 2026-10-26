from time import monotonic
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from django.db import IntegrityError, transaction
from django.utils import timezone
from sqlalchemy import inspect, text
from sqlalchemy.exc import NoSuchTableError

from transferdata.cli import engines, source_engine
from transferdata.procedures import execute_procedure, ProcedureReturnCodeError
from transferdata.etl import transfer
from transferdata.transfer_errors import diagnostic
from transferdata.schema import prepare_target, default_column_mapping
from .models import Job, Run
from transferdata.web.maintenance import is_paused
from django.core.exceptions import ValidationError


@transaction.atomic
def enqueue(job, action):
    if is_paused():
        raise ValidationError("Портал обновляется. Повторите запуск после завершения обновления.")
    if action not in dict(Run.ACTIONS):
        raise ValueError("Unknown action")
    job.full_clean()
    job = Job.objects.select_for_update().select_related("connection").get(pk=job.pk)
    job.full_clean()
    if job.archived or job.is_deleted:
        raise ValidationError("Задание находится в архиве или удалено.")
    if job.job_type == "procedure":
        if action == "run":
            action = "execute"
        if action not in {"check", "execute"}:
            raise ValidationError("Для процедуры доступны проверка подключения и выполнение.")
    elif action == "execute":
        raise ValidationError("Это задание переноса таблицы, а не процедуры.")
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
    for job in Job.objects.filter(enabled=True, archived=False, is_deleted=False).select_related("connection"):
        trigger = CronTrigger.from_crontab(job.cron, timezone=ZoneInfo(job.timezone))
        if job.next_run_at is None:
            Job.objects.filter(pk=job.pk, next_run_at=None).update(
                next_run_at=trigger.get_next_fire_time(None, now))
        elif job.next_run_at <= now:
            with transaction.atomic():
                try:
                    enqueue(job, "run")
                except ValidationError:
                    continue
                Job.objects.filter(pk=job.pk).update(next_run_at=trigger.get_next_fire_time(now, now))


def execute_next():
    run = Run.objects.filter(status="queued").order_by("created_at", "pk").first()
    if run is None:
        return False
    if not Run.objects.filter(pk=run.pk, status="queued").update(status="running", started_at=timezone.now()):
        return False
    started = monotonic()
    source = target = None
    stage = "Подключение к базам"

    last_progress = 0
    def progress(count, label):
        nonlocal last_progress
        now = monotonic()
        if now - last_progress < 1 and label.startswith('Запись'):
            return
        last_progress = now
        from django.db import DatabaseError
        try:
            Run.objects.filter(pk=run.pk, status='running').update(rows=count, message=label)
        except DatabaseError:
            pass  # Progress telemetry must not interrupt a data transaction.

    def preparation_step(label):
        if run.action != "prepare":
            return
        steps = run.details.setdefault("steps", [])
        if steps:
            steps[-1]["status"] = "success"
        steps.append({"label": label, "status": "running"})
        run.message = label
        run.save(update_fields=["details", "message"])

    try:
        config = run.snapshot
        job = config["jobs"][0]
        procedure = job.get("job_type") == "procedure"
        preparation_step("1. Проверка подключений")
        if procedure:
            source = source_engine(config)
        else:
            source, target = engines(config)
        if run.action == "execute":
            if not procedure:
                raise ValueError("Not a procedure job")
            stage = "Выполнение процедуры"
            run.details = {"return_code": execute_procedure(source, job)}
            run.message = "Процедура выполнена. Код возврата: 0."
        if run.action in {"check", "prepare"}:
            for engine in (source,) if procedure else (source, target):
                with engine.connect() as connection:
                    if connection.scalar(text("SELECT 1")) != 1:
                        raise RuntimeError("Unexpected database response")
            run.message = "Подключение SQL Server проверено." if procedure else "Оба подключения проверены."
        if run.action in {"inspect", "prepare"}:
            stage = "Получение структуры источника"
            preparation_step("2. Получение структуры источника")
            with source.connect() as connection:
                inspector = inspect(connection)
                columns = inspector.get_columns(job["source_table"], schema=job["source_schema"])
                run.details.update({"tables": inspector.get_table_names(schema=job["source_schema"]),
                               "columns": [{"name": c["name"], "type": str(c["type"]),
                                            "bytes": len(c["name"].encode("utf-8"))} for c in columns]})
            run.message = "Структура получена. Имена длиннее 63 UTF-8 байт нужно переименовать в маппинге."
        if run.action == "prepare":
            stage = "Сохранение столбцов задания"
            preparation_step("3. Заполнение столбцов задания")
            if not job.get("columns"):
                mapping = default_column_mapping(c["name"] for c in run.details["columns"])
                if not mapping:
                    raise ValueError("Source table has no columns")
                with transaction.atomic():
                    current = Job.objects.select_for_update().select_related("connection").get(pk=run.job_id)
                    latest = current.config()
                    if (latest["source"] != config["source"] or latest["target"] != config["target"] or
                        any(latest["jobs"][0].get(key) != value for key, value in job.items())):
                        raise ValidationError("Job changed during preparation")
                    current.columns = mapping
                    current.save(update_fields=["columns"])
                    job["columns"] = mapping
                    run.save(update_fields=["snapshot"])
            run.details["mapping"] = job["columns"]
            stage = "Создание таблицы назначения"
            preparation_step("4. Создание таблицы назначения")
            created = prepare_target(source, target, job)
            run.message = "Подключения проверены, структура получена, столбцы задания заполнены. " + ("Таблица создана." if created else "Таблица уже существует, изменений нет.")
        elif run.action == "run":
            stage = "Перенос данных"
            result = transfer(source, target, job, progress=progress)
            run.rows = result.rows
            run.message = "Полная загрузка завершена."
            if getattr(result, "cleaned_values", 0):
                run.details["text_cleanup"] = {"values": result.cleaned_values, "characters": result.removed_characters}
                run.message += f" Удалено нулевых символов: {result.removed_characters}; очищено значений: {result.cleaned_values}."
        elif run.action not in {"check", "inspect", "execute"}:
            raise ValueError("Unknown action")
        run.status = "success"
        if run.details.get("steps"):
            run.details["steps"][-1]["status"] = "success"
    except Exception as exc:
        # Driver exception text can contain row values, SQL and credentials.
        # Never persist it or print an exception traceback into the panel.
        run.status = "failed"
        if run.details.get("steps"):
            run.details["steps"][-1]["status"] = "failed"
        run.message = f"{stage}: ошибка {type(exc).__name__}. Проверьте подключения, права, имена и типы столбцов."
        if isinstance(exc, NoSuchTableError):
            side = getattr(exc, "transfer_side", "source" if run.action in {"inspect", "prepare"} else "unknown")
            run.details['missing_table_side'] = side
            if side == "target":
                run.message = (f"Таблица назначения {job['target_schema']}.{job['target_table']} не найдена "
                    "или недоступна. В списке заданий откройте «Ещё → Создать таблицу», "
                    "дождитесь успешного завершения и повторите загрузку. Если таблица уже существует, "
                    "проверьте базу, схему, точное имя и права в подключении PostgreSQL.")
            elif side == "source":
                run.message = (f"Таблица источника {job['source_schema']}.{job['source_table']} не найдена "
                    "или недоступна для чтения в выбранной базе SQL Server. Проверьте подключение, схему, "
                    "точное физическое имя таблицы и права.")
            else:
                run.message = ("Не удалось найти таблицу при подготовке переноса. Проверьте таблицу "
                    f"источника {job['source_schema']}.{job['source_table']} и назначения "
                    f"{job['target_schema']}.{job['target_table']}. Сторона ошибки не определена.")
        diagnosis = diagnostic(exc) if run.action == "run" else None
        if diagnosis:
            explanation, details = diagnosis
            run.details["error"] = details
            run.message = f"{stage}: {explanation} Загрузка отменена; изменения этой попытки не сохранены."
        if isinstance(exc, ProcedureReturnCodeError):
            run.details["return_code"] = exc.code
            run.message = f"Процедура не завершилась успешно. Код возврата: {exc.code if exc.code is not None else 'не получен'}."
    finally:
        for engine in (source, target):
            if engine is not None:
                engine.dispose()
    run.seconds = round(monotonic() - started, 3)
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "rows", "seconds", "message", "details", "finished_at"])
    return True
