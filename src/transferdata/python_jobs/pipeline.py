"""Sequential SQL steps. Durable checkpoints are separate from SQL transactions."""
import copy
import re
import time
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from transferdata.erp.control.models import DatabaseConnection
from transferdata.erp.engine.connections import connect
from .models import Run, StepRun
from .steps import validate_steps, procedure_sql
from .handlers import connection_from_snapshot


class ProcedureStepError(Exception):
    def __init__(self, code):
        self.return_code = code
        super().__init__('Nonzero procedure return code')


def prepare_snapshot(job, *, skip=(), resume_from=None, start_at=None):
    config = copy.deepcopy(resume_from.snapshot if resume_from else job.snapshot())
    inherited = {}
    if config['handler'] != 'sql_pipeline':
        if skip or resume_from:
            raise ValidationError('Управление шагами доступно только для цепочки SQL-процедур.')
        return config, inherited
    if resume_from:
        if resume_from.job_id != job.pk or resume_from.status in ('running', 'queued') or config['transaction_mode'] != 'per_step':
            raise ValidationError('Продолжение доступно только для завершённого запуска с фиксацией по шагам.')
        if job.handler != 'sql_pipeline':
            raise ValidationError('Задание больше не является цепочкой.')
        from transferdata.erp.control.onec import connection_fingerprint
        saved_connection = connection_from_snapshot(config['connection'])
        current_connection = DatabaseConnection.objects.filter(pk=saved_connection.pk, is_active=True).first()
        if not current_connection or connection_fingerprint(current_connection) != connection_fingerprint(saved_connection):
            raise ValidationError('Исходное подключение отключено или изменено. Проверьте настройки и создайте новый полный запуск.')
        keys = [s['id'] for s in config['steps']]
        if start_at not in keys or not config['steps'][keys.index(start_at)].get('enabled', True):
            raise ValidationError('Выберите включённый шаг начала.')
        previous = {s.key: s for s in resume_from.step_runs.all()}
        for step in config['steps'][:keys.index(start_at)]:
            old = previous.get(step['id'])
            if not old or old.status not in ('success', 'preserved', 'skipped'):
                raise ValidationError('Перед выбранным шагом есть незавершённые или неоднозначные шаги. Начните с них.')
            inherited[step['id']] = 'skipped' if old.status == 'skipped' else 'preserved'
    if skip:
        known = {s['id'] for s in config['steps']}
        if set(skip) - known or resume_from:
            raise ValidationError('Некорректный список пропускаемых шагов.')
        for step in config['steps']:
            if step['id'] in skip:
                step['enabled'] = False
    validate_steps(config['steps'])
    return config, inherited


def initialize(run, inherited):
    StepRun.objects.bulk_create([StepRun(run=run, key=s['id'], position=i, name=s['name'],
        status=inherited.get(s['id'], 'pending' if s.get('enabled', True) else 'skipped'))
        for i, s in enumerate(run.snapshot['steps'])])
    Run.objects.filter(pk=run.pk).update(total=len(run.snapshot['steps']),
        completed=run.step_runs.exclude(status='pending').count())


def request_stop(run):
    with transaction.atomic():
        run = Run.objects.select_for_update().get(pk=run.pk)
        if run.snapshot['handler'] != 'sql_pipeline' or run.status not in ('running', 'queued'):
            raise ValidationError('Этот запуск нельзя остановить по шагам.')
        if run.status == 'queued':
            Run.objects.filter(pk=run.pk, status='queued').update(status='stopped', stop_requested=True,
                finished_at=timezone.now(), message='Запуск отменён до выполнения первого шага.')
        else:
            Run.objects.filter(pk=run.pk).update(stop_requested=True, message='Запрошена остановка после текущего шага.')


def recover_steps():
    StepRun.objects.filter(run__status='running', status__in=['running', 'done']).update(
        status='unknown', finished_at=timezone.now(), message='Процесс прерван. Проверьте SQL-результат перед повтором.')


def execute_pipeline(run, progress):
    config = run.snapshot
    mode = config['transaction_mode']
    started = time.monotonic()
    current = None
    committing = False
    phase = 'configuration'
    def report(message):
        completed = run.step_runs.exclude(status__in=['pending', 'running']).count()
        Run.objects.filter(pk=run.pk).update(completed=completed, message=message)
        progress(completed=completed, total=len(config['steps']), message=message)
    def stop_requested():
        return Run.objects.filter(pk=run.pk, stop_requested=True).exists()
    def terminal(status, message):
        Run.objects.filter(pk=run.pk).update(status=status, message=message,
            finished_at=timezone.now(), seconds=time.monotonic()-started)
        report(message)
    def rollback(db):
        try:
            db.rollback()
            return True
        except Exception:
            return False
    try:
        validate_steps(config['steps'])
        connection = connection_from_snapshot(config['connection'])
        phase = 'connection'
        with connect(connection) as db:
            try:
                for index, step in enumerate(config['steps']):
                    current = run.step_runs.get(key=step['id'])
                    if current.status in ('preserved', 'skipped'):
                        continue
                    if stop_requested():
                        rolled = rollback(db)
                        run.step_runs.filter(status='done').update(status='rolled_back' if rolled else 'unknown')
                        terminal('stopped' if rolled else 'interrupted', 'Остановлено на границе шагов. ' + ('Фиксации предыдущих шагов сохранены.' if mode == 'per_step' else 'Запрошен откат общей транзакции; проверьте внутренние COMMIT процедур.'))
                        return
                    phase = 'dependencies'
                    required = set(step.get('depends_on', []))
                    successful = set(run.step_runs.filter(status__in=['success', 'done', 'preserved']).values_list('key', flat=True))
                    if required - successful:
                        raise ValidationError('Не выполнены обязательные шаги.')
                    current.status = 'running'; current.started_at = timezone.now(); current.save()
                    report(f'Шаг {index+1} из {len(config["steps"])}: {step["name"]}')
                    phase = 'procedure'
                    step_start = time.monotonic()
                    db.timeout = step.get('timeout', 300)
                    cursor = db.cursor()
                    try:
                        sql, values = procedure_sql(step, connection.database)
                        cursor.execute(sql, *values)
                        return_code = None
                        while True:
                            if cursor.description and len(cursor.description) == 1 and cursor.description[0][0] == '__etl_step_return_code':
                                row = cursor.fetchone()
                                return_code = row[0] if row else None
                            if not cursor.nextset():
                                break
                        if type(return_code) is not int:
                            raise RuntimeError('Missing procedure return code')
                        if return_code != 0:
                            raise ProcedureStepError(return_code)
                    finally:
                        cursor.close()
                    current.seconds = time.monotonic()-step_start
                    current.finished_at = timezone.now()
                    if mode == 'per_step':
                        phase = 'commit'
                        committing = True
                        db.commit()
                        # Commit and checkpoint are not a distributed transaction. Any failure is ambiguous.
                        current.status = 'success'; current.message = 'COMMIT выполнен.'; current.save()
                        committing = False
                    else:
                        current.status = 'done'; current.message = 'Ожидает COMMIT всей цепочки.'; current.save()
                    report(f'Шаг завершён: {step["name"]}')
                if stop_requested():
                    rolled = rollback(db)
                    run.step_runs.filter(status='done').update(status='rolled_back' if rolled else 'unknown')
                    terminal('stopped' if rolled else 'interrupted', 'Остановлено после последнего шага. ' + ('Шаги уже зафиксированы.' if mode == 'per_step' else 'Общая транзакция отменена; проверьте внутренние COMMIT процедур.'))
                    return
                if mode == 'atomic':
                    phase = 'commit'
                    committing = True
                    db.commit()
                    run.step_runs.filter(status='done').update(status='success', message='COMMIT общей транзакции выполнен.')
                terminal('success', 'Все выбранные шаги выполнены и зафиксированы.')
            except Exception as exc:
                rolled = rollback(db)
                if current and (current.status in ('running', 'done') or committing and current.status == 'success'):
                    current.status = 'unknown' if committing or not rolled else 'failed'
                    current.finished_at = timezone.now()
                    current.seconds = (current.finished_at-current.started_at).total_seconds() if current.started_at else None
                    current.message = 'Результат фиксации требует проверки.' if current.status == 'unknown' else (f'Процедура вернула код {exc.return_code}; следующие не запускались.' if isinstance(exc, ProcedureStepError) else 'Шаг завершился ошибкой; следующие не запускались.')
                    current.save()
                run.step_runs.filter(status='done').update(status='unknown' if committing or not rolled else 'rolled_back')
                raise
    except Exception as exc:
        # ODBC text can contain SQL, parameters or secrets. Retain category and SQLSTATE only.
        state = str(exc.args[0]) if exc.args else ''
        state = state if re.fullmatch(r'[A-Z0-9]{5}', state) else ''
        phase_label = {'configuration': 'Проверка настроек цепочки', 'connection': 'Подключение к SQL Server',
                       'dependencies': 'Проверка зависимостей', 'procedure': 'Выполнение процедуры', 'commit': 'Фиксация результатов'}.get(phase, 'Цепочка')
        hint = ''
        if isinstance(exc, ValidationError):
            if phase == 'connection':
                labels = {'name': 'название профиля', 'id': 'идентификатор профиля', 'server': 'сервер', 'port': 'порт',
                    'database': 'база', 'driver': 'ODBC-драйвер', 'authentication_type': 'тип входа',
                    'username': 'SQL-пользователь', 'credential_reference': 'имя переменной секрета',
                    'connection_timeout': 'таймаут подключения', 'query_timeout': 'таймаут запроса'}
                fields = [labels[k] for k in getattr(exc, 'error_dict', {}) if k in labels]
                hint = ' Проверьте поля профиля: ' + ', '.join(fields) + '.' if fields else ' Проверьте настройки профиля подключения.'
            elif phase == 'dependencies':
                hint = ' Не выполнены обязательные предыдущие шаги.'
            else:
                hint = ' Проверьте порядок, включение и зависимости шагов.'
        terminal('interrupted' if committing else 'failed',
            f'{phase_label}: {type(exc).__name__}' + (f' · код {exc.return_code}' if isinstance(exc, ProcedureStepError) else '') + (f' · SQLSTATE {state}' if state else '') + '.' + hint +
            (' SQL-процедуры ещё не запускались.' if phase in ('configuration', 'connection') else ' Проверьте результат текущего шага перед повтором.'))
