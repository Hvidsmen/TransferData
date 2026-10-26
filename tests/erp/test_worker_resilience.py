from unittest.mock import patch
import pytest
from django.db import OperationalError
from transferdata.erp.control.services import claim, enqueue

pytestmark = pytest.mark.django_db


def test_failed_claim_read_rolls_back_status_change(job):
    execution = enqueue(job)
    with patch('transferdata.erp.control.services.ETLExecution.objects.select_related') as read:
        read.return_value.get.side_effect = OperationalError('temporary read failure')
        with pytest.raises(OperationalError):
            claim()
    execution.refresh_from_db()
    assert execution.status == 'PENDING'
    assert execution.started_at is None
