import json
import os
from pathlib import Path
import subprocess
from uuid import uuid4, UUID
from django.conf import settings
from django.utils import timezone

TASK = 'ETL Dev — Update'
START = "$ErrorActionPreference='Stop'; $s=New-Object -ComObject Schedule.Service; $s.Connect(); $t=$s.GetFolder('\\').GetTask('ETL Dev '+[char]0x2014+' Update'); if(-not $t.Enabled -or $t.State -eq 4){throw 'Updater unavailable'}; $t.Run($null) | Out-Null"



class UpdateError(Exception):
    pass


def root():
    return settings.STATE_DIR / 'portal-update'


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    try:
        temp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def configured():
    if os.name != 'nt':
        return False
    try:
        data = json.loads((settings.STATE_DIR / 'portal-updater.json').read_text(encoding='utf-8-sig'))
        return data['version'] == 1 and data['task_name'] == TASK and Path(data['project_root']).resolve() == settings.STATE_DIR.parent.resolve()
    except (OSError, ValueError, KeyError, TypeError):
        return False


def status(run_id):
    run_id = UUID(str(run_id)).hex
    try:
        data = json.loads((root() / 'runs' / (run_id + '.json')).read_text(encoding='utf-8-sig'))
        if data.get('id') != run_id or data.get('version') != 1:
            return None
        return data
    except (OSError, ValueError, TypeError):
        return None


def latest():
    try:
        return status(json.loads((root() / 'request.json').read_text(encoding='utf-8-sig'))['id'])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def enqueue(user):
    if not configured():
        raise UpdateError('Сначала установите задачу обновления на сервере по инструкции ниже.')
    if (settings.STATE_DIR / 'maintenance.flag').exists():
        raise UpdateError('Портал уже находится в режиме обновления. Проверьте текущий запуск.')
    run_id = uuid4().hex
    directory = root()
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / 'request.lock'
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise UpdateError('Запрос обновления уже принят. Дождитесь его завершения; повторный запуск заблокирован.') from None
    requested = False
    try:
        with os.fdopen(fd, 'w', encoding='ascii') as stream:
            stream.write(run_id)
        now = timezone.now().isoformat()
        state = {'version':1, 'id':run_id, 'state':'queued', 'phase':'queued', 'step':0, 'total':11,
            'updated_at':now, 'requested_by':user.username, 'requested_at':now, 'events':[{'phase':'queued','at':now}]}
        atomic_json(directory / 'runs' / (run_id + '.json'), state)
        atomic_json(directory / 'request.json', {'version':1,'id':run_id, 'requested_at':now,
            'requested_by':user.username,'user_id':user.pk})
        requested = True
        subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',START],
            capture_output=True, check=True, timeout=20,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return run_id
    except (OSError, subprocess.SubprocessError) as exc:
        current = status(run_id) if requested else None
        if current and current.get('state') != 'queued':
            return run_id  # The independent updater has already acknowledged this request.
        if isinstance(exc, subprocess.TimeoutExpired):
            state.update(phase='launch_uncertain', updated_at=timezone.now().isoformat())
            atomic_json(directory / 'runs' / (run_id + '.json'), state)
            raise UpdateError('Ответ планировщика задержался. Запрос сохранён; повторный запуск заблокирован до проверки текущего обновления.') from None
        if requested:
            state.update(state='failed', phase='launch_failed', updated_at=timezone.now().isoformat())
            state['events'].append({'phase':'launch_failed','at':state['updated_at']})
            atomic_json(directory / 'runs' / (run_id + '.json'), state)
        lock.unlink(missing_ok=True)
        raise UpdateError('Не удалось запустить задачу «ETL Dev — Update». Проверьте установку, включение задачи и права её запуска. Текст системной ошибки не публикуется.') from None
