from datetime import datetime
from unittest.mock import patch
import pytest
from django.utils import timezone
from transferdata.erp.control.forms import JobForm
from transferdata.erp.control.services import enqueue_due_jobs

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize('cron,date,due', [
    ('30 9 * * 0,3', datetime(2026, 10, 22, 10), True),
    ('30 9 * * 0,3', datetime(2026, 10, 23, 10), False),
    ('0 2 15 * *', datetime(2026, 11, 15, 3), True),
    ('0 2 15 * *', datetime(2026, 11, 14, 3), False),
])
def test_erp_schedules_respect_selected_days_and_do_not_duplicate(job, cron, date, due):
    job.schedule_enabled = True
    job.schedule_cron = cron
    job.save()
    with patch('transferdata.erp.control.services.timezone.localtime', return_value=timezone.make_aware(date)):
        assert enqueue_due_jobs() == int(due)
        assert enqueue_due_jobs() == 0
    assert job.executions.count() == int(due)


def test_erp_legacy_time_is_shown_in_picker_without_changing_database(job):
    from datetime import time
    job.schedule_time = time(9, 45)
    job.save()
    form = JobForm(instance=job)
    assert form.initial['schedule_cron'] == '45 9 * * *'
    assert 'Еженедельно' in str(form['schedule_cron'])
    job.refresh_from_db()
    assert job.schedule_cron == ''
