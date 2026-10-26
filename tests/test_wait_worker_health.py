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
