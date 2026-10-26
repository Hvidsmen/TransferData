"""Small local worker diagnostics: no exception messages, SQL, records or credentials."""
import json
import threading
from django.conf import settings
from django.utils import timezone

_lock = threading.Lock()


def record(event, phase, error_type=''):
    payload = {'at': timezone.now().isoformat(), 'event': event, 'phase': phase, 'error_type': error_type}
    path = settings.STATE_DIR / 'erp-worker-events.jsonl'
    try:
        with _lock:
            if path.exists() and path.stat().st_size > 256 * 1024:
                path.replace(path.with_suffix('.previous.jsonl'))
            with path.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(payload) + '\n')
    except OSError:
        pass  # Diagnostics must not interrupt a transfer.
