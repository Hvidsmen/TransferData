from copy import copy
from uuid import UUID
from datetime import datetime
import hashlib
import json
from django.core.cache import cache
from django.utils import timezone
from transferdata.erp.engine.connections import connect

# Only Agent metadata is read. Never fetch job commands, proxies or credentials.
JOBS = '''SELECT CONVERT(varchar(36), j.job_id) AS id, j.name, j.enabled,
 c.name AS category, h.run_status AS last_status, h.run_date, h.run_time, h.run_duration,
 ok.run_date AS success_date, ok.run_time AS success_time,
 a.start_execution_date AS started_at,
 CASE WHEN a.start_execution_date IS NOT NULL AND a.stop_execution_date IS NULL THEN 1 ELSE 0 END AS active,
 a.next_scheduled_run_date AS next_run
 FROM msdb.dbo.sysjobs AS j
 LEFT JOIN msdb.dbo.syscategories AS c ON c.category_id = j.category_id AND c.category_class = 1
 OUTER APPLY (SELECT TOP (1) run_status, run_date, run_time, run_duration
 FROM msdb.dbo.sysjobhistory WHERE job_id = j.job_id AND step_id = 0 ORDER BY instance_id DESC) AS h
 OUTER APPLY (SELECT TOP (1) run_date, run_time FROM msdb.dbo.sysjobhistory
 WHERE job_id = j.job_id AND step_id = 0 AND run_status = 1 ORDER BY instance_id DESC) AS ok
 LEFT JOIN msdb.dbo.sysjobactivity AS a ON a.job_id = j.job_id AND a.session_id =
 (SELECT TOP (1) session_id FROM msdb.dbo.syssessions ORDER BY agent_start_date DESC, session_id DESC)
 ORDER BY j.name, j.job_id'''
SCHEDULES = '''SELECT CONVERT(varchar(36), js.job_id) AS job_id, s.name, s.enabled, s.freq_type,
 s.freq_interval, s.freq_subday_type, s.freq_subday_interval, s.freq_relative_interval,
 s.freq_recurrence_factor, s.active_start_time, s.active_end_time, s.active_start_date, s.active_end_date
 FROM msdb.dbo.sysjobschedules AS js
 INNER JOIN msdb.dbo.sysschedules AS s ON s.schedule_id = js.schedule_id'''
HISTORY = '''SELECT TOP (50) instance_id, run_status, run_date, run_time, run_duration
 FROM msdb.dbo.sysjobhistory WHERE job_id = CONVERT(uniqueidentifier, ?) AND step_id = 0
 ORDER BY instance_id DESC'''
STEPS = '''SELECT step_id, step_name, subsystem, database_name, retry_attempts
 FROM msdb.dbo.sysjobsteps WHERE job_id = CONVERT(uniqueidentifier, ?) ORDER BY step_id'''
STATUSES = {0: ('failed', 'Ошибка'), 1: ('success', 'Успешно'), 2: ('queued', 'Повторная попытка'),
            3: ('cancelled', 'Отменено'), 4: ('running', 'Выполняется')}


def rows(cursor, sql, parameters=()):
    cursor.execute(sql, parameters) if parameters else cursor.execute(sql)
    names = [col[0] for col in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def stamp(day, time):
    try:
        return datetime.strptime(f'{int(day):08d}{int(time or 0):06d}', '%Y%m%d%H%M%S')
    except (ValueError, TypeError):
        return None


def duration(value):
    if value is None:
        return '—'
    value = int(value)
    hours, rest = divmod(value, 10000)
    minutes, seconds = divmod(rest, 100)
    return f'{hours}:{minutes:02d}:{seconds:02d}'


def clock(value):
    value = int(value or 0)
    base = f'{value // 10000:02d}:{value // 100 % 100:02d}'
    return base + (f':{value % 100:02d}' if value % 100 else '')


def schedule_label(schedule):
    kind, interval = schedule['freq_type'], schedule['freq_interval']
    repeat = schedule.get('freq_recurrence_factor') or 1
    if kind == 1:
        label = 'Однократно'
    elif kind == 4:
        label = 'Ежедневно' if interval == 1 else f'Каждые {interval} дн.'
    elif kind == 8:
        days = [name for bit, name in [(2,'Пн'),(4,'Вт'),(8,'Ср'),(16,'Чт'),(32,'Пт'),(64,'Сб'),(1,'Вс')] if interval & bit]
        label = ('Еженедельно' if repeat == 1 else f'Каждые {repeat} нед.') + ' · ' + ', '.join(days)
    elif kind == 16:
        label = f'{interval}-го числа · каждые {repeat} мес.'
    elif kind == 32:
        relative = {1:'Первый',2:'Второй',4:'Третий',8:'Четвёртый',16:'Последний'}.get(schedule['freq_relative_interval'], 'Порядок ' + str(schedule['freq_relative_interval']))
        day = {1:'воскресенье',2:'понедельник',3:'вторник',4:'среда',5:'четверг',6:'пятница',7:'суббота',8:'день',9:'рабочий день',10:'выходной день'}.get(interval, str(interval))
        label = f'{relative} {day} · каждые {repeat} мес.'
    elif kind == 64:
        return 'При запуске SQL Server Agent'
    elif kind == 128:
        return 'При простое сервера'
    else:
        return f'Особое расписание (тип {kind})'
    subday = schedule.get('freq_subday_type')
    if subday in (2, 4, 8):
        units = {2:'с',4:'мин',8:'ч'}
        label += f" · каждые {schedule['freq_subday_interval']} {units[subday]} · {clock(schedule['active_start_time'])}–{clock(schedule['active_end_time'])}"
    else:
        label += ' · ' + clock(schedule['active_start_time'])
    return label


def decorate(record):
    record['status'], record['label'] = ('running', 'Есть незавершённая активность') if record.get('active') else STATUSES.get(record.get('last_status', record.get('run_status')), ('never', 'Нет истории'))
    record['last_result_label'] = STATUSES.get(record.get('last_status', record.get('run_status')), ('never', 'Нет истории'))[1]
    record['last_run'] = stamp(record.get('run_date'), record.get('run_time'))
    record['last_success'] = stamp(record.get('success_date'), record.get('success_time'))
    record['duration'] = duration(record.get('run_duration'))
    return record


def key(config):
    fields = ('pk','server','port','database','driver','authentication_type','username','credential_reference','trust_server_certificate','updated_at')
    raw = json.dumps([str(getattr(config, field)) for field in fields], ensure_ascii=True)
    return 'sql-agent:v3:' + hashlib.sha256(raw.encode()).hexdigest()


def read(config, job_id=None):
    cache_key = key(config) + (':' + str(job_id) if job_id else '')
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    connection = copy(config)
    connection.database = 'msdb'
    connection.connection_timeout = min(config.connection_timeout, 3)
    connection.query_timeout = 5
    with connect(connection, timeout=5) as database:
        cursor = database.cursor()
        try:
            jobs = rows(cursor, JOBS)
            schedules = rows(cursor, SCHEDULES)
            index = {}
            for schedule in schedules:
                schedule['label'] = schedule_label(schedule)
                schedule['start_date'] = stamp(schedule.get('active_start_date'), 0)
                schedule['end_date'] = stamp(schedule.get('active_end_date'), 0)
                index.setdefault(schedule['job_id'].lower(), []).append(schedule)
            for job in jobs:
                # SQL Server returns uppercase GUID text; Django's UUID route requires canonical lowercase.
                job['id'] = str(UUID(str(job['id'])))
                decorate(job)
                job['schedules'] = index.get(job['id'].lower(), [])
            result = {'jobs': jobs, 'observed_at': timezone.now()}
            if job_id:
                selected = next((job for job in jobs if job['id'].lower() == str(job_id).lower()), None)
                result['job'] = selected
                result['history'] = [decorate(row) for row in rows(cursor, HISTORY, (str(job_id),))] if selected else []
                result['steps'] = rows(cursor, STEPS, (str(job_id),)) if selected else []
        finally:
            cursor.close()
    cache.set(cache_key, result, 15)
    return result
