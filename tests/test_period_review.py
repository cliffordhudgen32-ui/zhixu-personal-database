from datetime import date, datetime

from sqlalchemy import select, func

from app.models import DailyEntry
from app.services import workflow, period_review
from app.services.records import save
from app.workflow_models import AIDraft, AIArchive
from test_ai_workflow import database, fake_provider, read, CONFIG


def record(factory, day, content, **options):
    with factory.begin() as session:
        return save(session, 'entries', {'date': day, 'title': '周期原始记录', 'content': content, **options}).uuid


def test_week_range_and_month_leap_year():
    assert period_review.scope('week', date(2030, 1, 2)) == 'week:2029-12-31'
    assert period_review.date_range('week:2029-12-31') == (date(2029, 12, 31), date(2030, 1, 6))
    assert period_review.date_range('month:2032-02-01')[1] == date(2032, 2, 29)


def test_period_draft_uses_only_range_and_human_approval(database):
    included = record(database, date(2030, 1, 2), '本周检查参数，已完成排查，计划继续观察。')
    record(database, date(2029, 12, 29), '之前的资料不属于这周。')
    record(database, date(2030, 1, 3), '敏感资料不能进入周期整理。', privacy_level=3)
    record(database, date(2030, 1, 3), '已有自动归档不会二次复制。', metadata_json={'ai_workflow_archive': True})
    queued = workflow.enqueue('week:2029-12-31', CONFIG)
    assert [source['uuid'] for source in queued['source_records']] == [included]
    workflow.process_draft(queued['uuid'], provider=fake_provider)
    draft = read(database, AIDraft, queued['uuid'])
    assert draft['status'] == 'pending_review' and draft['category'] == '每周复盘'
    assert '2029-12-31 至 2030-01-06' in draft['title']
    assert workflow.enqueue('week:2029-12-31', CONFIG)['uuid'] == draft['uuid']
    with database() as session:
        assert session.scalar(select(func.count()).select_from(AIArchive)) == 0
    approved = workflow.approve(draft['uuid'], draft['revision'], True)
    assert approved['status'] == 'approved'
    assert read(database, DailyEntry, included)['content'].startswith('本周检查参数')


def test_auto_periods_only_latest_completed_periods():
    config = dict(CONFIG, weekly_enabled=True, monthly_enabled=True, daily_time='21:00')
    assert period_review.scheduled_scopes(datetime(2030, 2, 1, 20, 59), config) == []
    assert period_review.scheduled_scopes(datetime(2030, 2, 1, 21, 0), config) == ['week:2030-01-21', 'month:2030-01-01']


def test_later_source_change_blocks_period_approval(database):
    uid = record(database, date(2030, 1, 2), '本月现场检查，完成记录。')
    queued = workflow.enqueue('month:2030-01-01', CONFIG)
    workflow.process_draft(queued['uuid'], provider=fake_provider)
    draft = read(database, AIDraft, queued['uuid'])
    record(database, date(2030, 1, 15), '后来补录的资料需要纳入新草稿。')
    import pytest
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as failure:
        workflow.approve(draft['uuid'], draft['revision'], True)
    assert failure.value.status_code == 409
