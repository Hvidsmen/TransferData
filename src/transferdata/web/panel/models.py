from zoneinfo import ZoneInfo
from apscheduler.triggers.cron import CronTrigger
from django.core.exceptions import ValidationError
from django.db import models


class Connection(models.Model):
    name = models.CharField("Название подключения", max_length=150, unique=True)
    source_host = models.CharField("Сервер SQL Server", max_length=255, default="vm-dwh")
    source_database = models.CharField("База SQL Server", max_length=255, default="DataWH")
    source_driver = models.CharField("ODBC-драйвер", max_length=150, default="ODBC Driver 17 for SQL Server")
    source_trust = models.BooleanField("Доверять сертификату SQL Server без проверки", default=False)
    target_host = models.CharField("Сервер PostgreSQL", max_length=255)
    target_port = models.PositiveIntegerField("Порт PostgreSQL", default=5432)
    target_database = models.CharField("База PostgreSQL", max_length=255)
    username_env = models.CharField("Переменная с логином PostgreSQL", max_length=100, default="TARGET_USER")
    password_env = models.CharField("Переменная с паролем PostgreSQL", max_length=100, default="TARGET_PASSWORD",
                                  help_text="Значение хранится вне панели: в окружении worker или Windows DPAPI.")
    sslmode = models.CharField("Проверка TLS PostgreSQL", max_length=20, default="verify-full", choices=[
        ("verify-full", "Проверять сертификат и имя"), ("verify-ca", "Проверять центр сертификации"),
        ("require", "Шифровать без проверки сертификата*"),
    ], help_text="* libpq может проверять CA при наличии root.crt или PGSSLROOTCERT.")

    class Meta:
        verbose_name = "Подключение"
        verbose_name_plural = "Подключения"

    def __str__(self):
        return self.name

    def config(self):
        return {"source": {"auth": "windows", "host": self.source_host,
                           "database": self.source_database, "driver": self.source_driver,
                           "trust_server_certificate": self.source_trust},
                "target": {"dialect": "postgresql", "host": self.target_host, "port": self.target_port,
                           "database": self.target_database, "username_env": self.username_env,
                           "password_env": self.password_env, "sslmode": self.sslmode}}


class Job(models.Model):
    name = models.CharField("Название задания", max_length=150, unique=True)
    connection = models.ForeignKey(Connection, on_delete=models.PROTECT, verbose_name="Подключение")
    source_schema = models.CharField("Схема SQL Server", max_length=128, default="erp")
    source_table = models.CharField("Таблица SQL Server", max_length=128)
    target_schema = models.CharField("Схема PostgreSQL", max_length=63, default="public")
    target_table = models.CharField("Таблица PostgreSQL", max_length=63)
    cron = models.CharField("Расписание cron", max_length=100, default="0 2 * * *",
                           help_text="Минута час день месяц день-недели. Каждый день в 02:00: 0 2 * * *")
    timezone = models.CharField("Часовой пояс", max_length=100, default="Europe/Moscow")
    enabled = models.BooleanField("Включить расписание", default=False)
    batch_size = models.PositiveIntegerField("Строк в пакете", default=1000)
    allow_empty = models.BooleanField("Разрешить очистку при пустом источнике", default=False)
    columns = models.JSONField("Соответствие столбцов", default=dict, blank=True,
                               help_text='JSON «источник»: «назначение». Пустой объект — все столбцы с исходными именами.')
    next_run_at = models.DateTimeField("Следующий запуск", null=True, blank=True, editable=False)

    class Meta:
        verbose_name = "Задание"
        verbose_name_plural = "Задания"

    def __str__(self):
        return self.name

    def clean(self):
        errors = {}
        try:
            CronTrigger.from_crontab(self.cron, timezone=ZoneInfo(self.timezone))
        except (ValueError, KeyError):
            errors["cron"] = "Проверьте cron и часовой пояс."
        if self.batch_size < 1:
            errors["batch_size"] = "Размер пакета должен быть больше нуля."
        if not isinstance(self.columns, dict) or not all(
            isinstance(k, str) and k and isinstance(v, str) and v for k, v in self.columns.items()
        ):
            errors["columns"] = "Нужен JSON-объект с непустыми строковыми именами."
        elif len(set(self.columns.values())) != len(self.columns):
            errors["columns"] = "Имена назначения не должны повторяться."
        else:
            if any(len(v.encode("utf-8")) > 63 for v in self.columns.values()):
                errors["columns"] = "Имя столбца PostgreSQL должно быть не длиннее 63 UTF-8 байт."
        for field in ("target_schema", "target_table"):
            if len(getattr(self, field).encode("utf-8")) > 63:
                errors[field] = "Имя PostgreSQL должно быть не длиннее 63 UTF-8 байт."
        if errors:
            raise ValidationError(errors)

    def config(self):
        config = self.connection.config()
        config.update({"timezone": self.timezone, "jobs": [{
            "name": self.name, "source_schema": self.source_schema, "source_table": self.source_table,
            "target_schema": self.target_schema, "target_table": self.target_table,
            "cron": self.cron, "batch_size": self.batch_size, "allow_empty": self.allow_empty,
            **({"columns": self.columns} if self.columns else {}),
        }]})
        return config


class Run(models.Model):
    ACTIONS = [("check", "Проверить подключения"), ("inspect", "Получить структуру источника"),
               ("prepare", "Создать таблицу"), ("run", "Полная загрузка")]
    STATUS = [("queued", "В очереди"), ("running", "Выполняется"),
              ("success", "Успешно"), ("failed", "Ошибка"), ("interrupted", "Прерван, проверить результат")]
    job = models.ForeignKey(Job, on_delete=models.PROTECT, verbose_name="Задание")
    action = models.CharField("Действие", max_length=20, choices=ACTIONS)
    status = models.CharField("Состояние", max_length=20, choices=STATUS, default="queued")
    snapshot = models.JSONField(editable=False)
    created_at = models.DateTimeField("Поставлен в очередь", auto_now_add=True)
    started_at = models.DateTimeField("Начало", null=True)
    finished_at = models.DateTimeField("Завершение", null=True)
    rows = models.PositiveBigIntegerField("Строк", null=True)
    seconds = models.FloatField("Секунд", null=True)
    message = models.TextField("Результат", blank=True)
    details = models.JSONField("Структура источника", default=dict, blank=True)

    class Meta:
        verbose_name = "Запуск"
        verbose_name_plural = "История запусков"
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["job"], condition=models.Q(status__in=["queued", "running"]),
                                                name="one_active_run_per_job")]

    def __str__(self):
        return f"{self.job} — {self.get_status_display()}"
