from datetime import datetime, timezone
import pytest
from transferdata.scheduling import simple_schedule, due_today, schedule_label
from apscheduler.triggers.cron import CronTrigger


@pytest.mark.parametrize('cron,now,due', [
    ('30 9 * * *', '2026-10-22T09:29:00', False),
    ('30 9 * * *', '2026-10-22T09:30:00', True),
    ('30 9 * * 0,3', '2026-10-22T10:00:00', True),
    ('30 9 * * 0,3', '2026-10-23T10:00:00', False),
    ('0 2 15 * *', '2026-11-15T02:00:00', True),
    ('0 2 15 * *', '2026-11-16T02:00:00', False),
    ('0 2 31 * *', '2026-04-30T12:00:00', False),
    ('0 2 31 * *', '2026-05-31T02:00:00', True),
])
def test_daily_weekly_monthly_dates(cron, now, due):
    assert due_today(cron, datetime.fromisoformat(now)) is due


@pytest.mark.parametrize('cron', ['10 * * *', '0 24 * * *', '60 2 * * *',
                                 '0 2 32 * *', '0 2 * * 7', '*/10 * * * *'])
def test_erp_rejects_unsupported_or_invalid_schedule(cron):
    with pytest.raises(ValueError):
        simple_schedule(cron)


def test_transferdata_month_end_skips_short_months():
    trigger = CronTrigger.from_crontab('0 2 31 * *', timezone='Europe/Moscow')
    result = trigger.get_next_fire_time(None, datetime(2026, 4, 1, tzinfo=timezone.utc))
    assert (result.month, result.day, result.hour) == (5, 31, 2)


def test_schedule_is_described_without_cron():
    assert schedule_label('30 9 * * *') == 'Ежедневно в 09:30'
    assert schedule_label('30 9 * * 0,4') == 'Пн, Пт в 09:30'
    assert '15 числа' in schedule_label('0 2 15 * *')
