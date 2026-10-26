from datetime import timedelta
from unittest.mock import patch
import pytest
from django.core.management import call_command, CommandError
from django.utils import timezone

MODULE = 'transferdata.web.panel.management.commands.wait_worker_health'


def test_requires_new_signal_even_when_old_signal_is_still_online():
    since = timezone.now()
    old = {'key': 'onec', 'online': True, 'heartbeat': since - timedelta(seconds=2)}
    new = {**old, 'heartbeat': since + timedelta(seconds=1)}
    with patch(MODULE + '.worker_health', side_effect=[[old], [new]]), patch(MODULE + '.time.sleep'):
        call_command('wait_worker_health', since=since.isoformat(), worker=['onec'])


def test_missing_worker_fails_instead_of_reporting_success():
    with patch(MODULE + '.worker_health', return_value=[]), patch(MODULE + '.time.monotonic', side_effect=[0, 2]):
        with pytest.raises(CommandError, match='onec'):
            call_command('wait_worker_health', since=timezone.now().isoformat(), worker=['onec'], timeout=1)


def test_pause_does_not_invalidate_a_fresh_worker_signal():
    since = timezone.now()
    with patch(MODULE + '.worker_health', return_value=[{'key': 'onec', 'online': True, 'state': 'paused', 'heartbeat': since}]):
        call_command('wait_worker_health', since=since.isoformat(), worker=['onec'])


@pytest.mark.django_db
def test_erp_reports_liveness_while_maintenance_blocks_work():
    from transferdata.erp.control.models import WorkerLease
    erp = 'transferdata.erp.control.management.commands.etl_worker'
    with patch(erp + '.worker_paused', return_value=True), patch(erp + '.enqueue_due_jobs') as schedule, patch(erp + '.claim') as claim:
        call_command('etl_worker', once=True)
    assert WorkerLease.objects.get(pk=1).heartbeat >= timezone.now() - timedelta(seconds=5)
    schedule.assert_not_called()
    claim.assert_not_called()


@pytest.mark.django_db
def test_erp_retries_idle_database_error_without_claiming_or_replaying_job():
    from django.db import OperationalError
    erp = 'transferdata.erp.control.management.commands.etl_worker'
    with patch(erp + '.worker_paused', side_effect=[False, True]), patch(erp + '.enqueue_due_jobs', side_effect=OperationalError('private SQL text')), patch(erp + '.claim') as claim, patch(erp + '.record') as record, patch(erp + '.time.sleep', side_effect=[None, KeyboardInterrupt]):
        with pytest.raises(KeyboardInterrupt):
            call_command('etl_worker')
    claim.assert_not_called()
    record.assert_any_call('retrying', 'poll', 'OperationalError')
    assert 'private' not in str(record.call_args_list)


def test_diagnostics_keep_only_category_and_bound_log_size(settings, tmp_path):
    import json
    from transferdata.erp.control.worker_diagnostics import record
    settings.STATE_DIR = tmp_path
    record('failed', 'execute', 'OperationalError')
    path = tmp_path / 'erp-worker-events.jsonl'
    event = json.loads(path.read_text())
    assert set(event) == {'at', 'event', 'phase', 'error_type'}
    path.write_text('x' * (256*1024+1))
    record('started', 'startup')
    assert json.loads(path.read_text())['event'] == 'started'
    assert path.with_suffix('.previous.jsonl').exists()
