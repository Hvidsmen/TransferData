import json
from pathlib import Path
from zoneinfo import ZoneInfo
from apscheduler.triggers.cron import CronTrigger
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models

HANDLERS = [('commercial_headers', 'Заголовки коммерческих предложений'), ('script', 'Python-файл на сервере')]


def script_path(value):
    root = (settings.STATE_DIR / 'python-scripts').resolve()
    path = (root / value).resolve()
    if not value or Path(value).is_absolute() or not path.is_relative_to(root) or path.suffix.lower() != '.py' or not path.is_file():
        raise ValidationError('Укажите существующий .py файл внутри .state/python-scripts, например reports/daily.py.')
    return path


class Job(models.Model):
    name = models.CharField('Название задания', max_length=150, unique=True)
    group = models.ForeignKey('panel.JobGroup', verbose_name='Группа заданий', null=True, blank=True, on_delete=models.SET_NULL, related_name='python_jobs')
    handler = models.CharField('Обработчик', max_length=40, choices=HANDLERS, default='commercial_headers')
    connection = models.ForeignKey('control.DatabaseConnection', verbose_name='Подключение SQL Server', null=True, blank=True, on_delete=models.PROTECT)
    script = models.CharField('Python-файл', max_length=250, blank=True, help_text='Путь относительно .state/python-scripts. Только для обработчика «Python-файл на сервере».')
    parameters = models.JSONField('Параметры', default=dict, blank=True, help_text='JSON-объект. Для заголовков: {"date_from": "2023-01-01"}. Для файла передаётся через ETL_JOB_PARAMETERS.')
    timeout = models.PositiveIntegerField('Максимальное время выполнения, секунд', default=3600, validators=[MinValueValidator(1), MaxValueValidator(86400)])
    cron = models.CharField('Расписание', max_length=100, default='0 2 * * *')
    timezone = models.CharField('Часовой пояс', max_length=100, default='Europe/Moscow')
    enabled = models.BooleanField('Включить расписание', default=False)
    next_run_at = models.DateTimeField(null=True, blank=True, editable=False)
    archived = models.BooleanField(default=False, editable=False)
    is_deleted = models.BooleanField(default=False, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    def clean(self):
        from .handlers import validate_parameters
        try:
            encoded = json.dumps(self.parameters, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):
            raise ValidationError({'parameters': 'Нужен корректный JSON без NaN и Infinity.'}) from None
        if not isinstance(self.parameters, dict) or len(encoded.encode('utf-8')) > 65536:
            raise ValidationError({'parameters': 'Нужен JSON-объект не больше 64 КБ.'})
        if self.handler == 'commercial_headers':
            validate_parameters(self.parameters)
            if not self.connection_id or not self.connection.is_active:
                raise ValidationError({'connection': 'Выберите активное подключение к DataWH с правами чтения и записи заголовков.'})
            if self.connection.database.casefold() != 'datawh':
                raise ValidationError({'connection': 'Этот обработчик рассчитан на базу DataWH.'})
        elif self.handler == 'script':
            script_path(self.script)
        else:
            raise ValidationError({'handler': 'Неизвестный обработчик.'})
        try:
            CronTrigger.from_crontab(self.cron, timezone=ZoneInfo(self.timezone))
        except (ValueError, KeyError):
            raise ValidationError({'cron': 'Проверьте расписание и часовой пояс.'}) from None

    @property
    def schedule_label(self):
        from transferdata.scheduling import schedule_label
        return schedule_label(self.cron)

    def snapshot(self):
        connection = None
        if self.connection_id:
            connection = {f.attname: getattr(self.connection, f.attname) for f in self.connection._meta.concrete_fields if f.name not in ('created_at', 'updated_at')}
        return {'handler': self.handler, 'script': self.script, 'parameters': self.parameters,
                'timeout': self.timeout, 'connection': connection}


class Run(models.Model):
    STATUS = [('queued', 'В очереди'), ('running', 'Выполняется'), ('success', 'Успешно'), ('failed', 'Ошибка'), ('interrupted', 'Прерван: проверьте результат')]
    job = models.ForeignKey(Job, on_delete=models.PROTECT)
    snapshot = models.JSONField(editable=False)
    status = models.CharField(max_length=20, choices=STATUS, default='queued')
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)
    completed = models.PositiveBigIntegerField(default=0)
    total = models.PositiveBigIntegerField(null=True)
    rows = models.PositiveBigIntegerField(default=0)
    message = models.TextField(blank=True)
    output = models.TextField(blank=True)
    seconds = models.FloatField(null=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        constraints = [models.UniqueConstraint(fields=['job'], condition=models.Q(status__in=['queued', 'running']), name='python_one_active_run')]

    @property
    def percent(self):
        return min(100, int(100 * self.completed / self.total)) if self.total else (100 if self.status == 'success' else 0)
