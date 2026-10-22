from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator, RegexValidator
from django.db import models
from django.db.models import Q
from transferdata.erp.engine.validators import identifier, table_name, read_query

class DatabaseConnection(models.Model):
    name = models.CharField(max_length=150, unique=True)
    description = models.TextField(blank=True)
    server = models.CharField(max_length=255)
    port = models.PositiveIntegerField(default=1433, validators=[MinValueValidator(1), MaxValueValidator(65535)])
    database = models.CharField(max_length=128)
    authentication_type = models.CharField(max_length=10, choices=[('WINDOWS', 'Windows'), ('SQL', 'SQL Server')], default='SQL')
    username = models.CharField(max_length=128, blank=True)
    credential_reference = models.CharField(max_length=128, blank=True, validators=[RegexValidator(r'^[A-Za-z_][A-Za-z0-9_]*$')])
    driver = models.CharField(max_length=128, default='ODBC Driver 18 for SQL Server')
    connection_timeout = models.PositiveIntegerField(default=10, validators=[MinValueValidator(1), MaxValueValidator(30)])
    query_timeout = models.PositiveIntegerField(default=300, validators=[MinValueValidator(1), MaxValueValidator(86400)])
    role = models.CharField(max_length=10, choices=[('SOURCE', 'Source'), ('TARGET', 'Target'), ('BOTH', 'Both')], default='SOURCE')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    def clean(self):
        if self.authentication_type == 'SQL' and (not self.username or not self.credential_reference):
            raise ValidationError('Для SQL Authentication укажите пользователя и имя переменной секрета.')
    def __str__(self):
        return self.name

class ETLJob(models.Model):
    name = models.CharField(max_length=150)
    group = models.ForeignKey('panel.JobGroup', verbose_name='Группа заданий', null=True, blank=True,
                              on_delete=models.SET_NULL)
    archived = models.BooleanField(default=False, editable=False)
    is_deleted = models.BooleanField(default=False, editable=False)
    description = models.TextField(blank=True)
    source_connection = models.ForeignKey(DatabaseConnection, on_delete=models.PROTECT, related_name='source_jobs')
    target_connection = models.ForeignKey(DatabaseConnection, on_delete=models.PROTECT, related_name='target_jobs')
    source_type = models.CharField(max_length=12, choices=[('TABLE', 'Таблица'), ('SQL_QUERY', 'SQL-запрос')], default='TABLE')
    source_table = models.CharField(max_length=257, blank=True)
    sql_query = models.TextField(blank=True)
    target_schema = models.CharField(max_length=128, default='dbo')
    target_table = models.CharField(max_length=128)
    load_mode = models.CharField(max_length=12, choices=[('FULL', 'Full Load'), ('INCREMENTAL', 'В разработке')], default='FULL')
    batch_size = models.PositiveIntegerField(default=10000, validators=[MinValueValidator(1), MaxValueValidator(100000)])
    command_timeout = models.PositiveIntegerField(default=300, validators=[MinValueValidator(1), MaxValueValidator(86400)])
    is_active = models.BooleanField(default=True)
    schedule_enabled = models.BooleanField(default=False)
    schedule_time = models.TimeField(null=True, blank=True)
    schedule_cron = models.CharField('Расписание', max_length=100, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    def clean(self):
        identifier(self.target_schema)
        identifier(self.target_table)
        table_name(self.source_table) if self.source_type == 'TABLE' else read_query(self.sql_query)
        if self.schedule_cron:
            from transferdata.scheduling import simple_schedule
            try:
                simple_schedule(self.schedule_cron)
            except ValueError as exc:
                raise ValidationError({'schedule_cron': str(exc)}) from None
        if self.schedule_enabled and not self.schedule_time and not self.schedule_cron:
            raise ValidationError('Укажите время расписания.')
        if self.source_connection_id and (not self.source_connection.is_active or self.source_connection.role == 'TARGET'):
            raise ValidationError('Источник неактивен или имеет неверную роль.')
        if self.target_connection_id and (not self.target_connection.is_active or self.target_connection.role == 'SOURCE'):
            raise ValidationError('Назначение неактивно или имеет неверную роль.')
        if self.source_connection_id and self.target_connection_id:
            a, b = self.source_connection, self.target_connection
            if (a.server.lower(), a.port, a.database.lower()) == (b.server.lower(), b.port, b.database.lower()):
                raise ValidationError('Источник и назначение должны быть отдельными базами.')
    def runnable(self):
        self.full_clean()
        if self.archived or self.is_deleted:
            raise ValidationError('Задание находится в архиве или удалено.')
        if not self.is_active or self.load_mode != 'FULL':
            raise ValidationError('Задание отключено или Incremental Load ещё не реализован.')
    @property
    def schedule_label(self):
        from transferdata.scheduling import schedule_label
        if self.schedule_cron:
            return schedule_label(self.schedule_cron)
        return 'Ежедневно в ' + self.schedule_time.strftime('%H:%M') if self.schedule_time else 'Не задано'
    def __str__(self):
        return self.name

class ETLExecution(models.Model):
    STATUSES = [(s, s) for s in ['PENDING', 'RUNNING', 'SUCCESS', 'FAILED', 'CANCELLED']]
    job = models.ForeignKey(ETLJob, on_delete=models.PROTECT, related_name='executions')
    status = models.CharField(max_length=10, choices=STATUSES, default='PENDING', db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.FloatField(null=True, blank=True)
    rows_extracted = models.PositiveBigIntegerField(default=0)
    rows_loaded = models.PositiveBigIntegerField(default=0)
    error_message = models.TextField(blank=True)
    stage = models.CharField(max_length=200, default='В очереди')
    triggered_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    trigger_type = models.CharField(max_length=10, choices=[('MANUAL', 'Manual'), ('SCHEDULED', 'Scheduled')], default='MANUAL')
    schedule_key = models.CharField(max_length=80, null=True, blank=True, unique=True)
    heartbeat = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    @property
    def heartbeat_stale(self):
        from datetime import timedelta
        from django.utils import timezone
        return self.status == 'RUNNING' and (self.heartbeat is None or self.heartbeat < timezone.now() - timedelta(seconds=60))

    class Meta:
        constraints = [models.UniqueConstraint(fields=['job'], condition=Q(status__in=['PENDING', 'RUNNING']), name='one_active_execution_per_job')]
        ordering = ['-created_at']

class ETLExecutionLog(models.Model):
    execution = models.ForeignKey(ETLExecution, on_delete=models.PROTECT, related_name='logs')
    timestamp = models.DateTimeField(auto_now_add=True)
    level = models.CharField(max_length=10, choices=[(x, x) for x in ['INFO', 'WARNING', 'ERROR']])
    message = models.TextField()
    rows_processed = models.PositiveBigIntegerField(default=0)
    class Meta:
        ordering = ['timestamp', 'id']

class ETLAuditLog(models.Model):
    timestamp = models.DateTimeField(auto_now_add=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    object_type = models.CharField(max_length=40)
    object_id = models.PositiveBigIntegerField()
    action = models.CharField(max_length=30)
    # SQL and credentials deliberately excluded from audit messages.

class WorkerLease(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    owner = models.CharField(max_length=36, blank=True)
    heartbeat = models.DateTimeField(null=True)
