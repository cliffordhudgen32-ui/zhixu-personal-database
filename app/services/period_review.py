"""Calendar week/month drafts use the same explicit human review as daily drafts."""
from calendar import monthrange
from datetime import date, timedelta

from fastapi import HTTPException


def scope(kind, anchor):
    if kind == 'week':
        return 'week:' + (anchor - timedelta(days=anchor.weekday())).isoformat()
    if kind == 'month':
        return 'month:' + anchor.replace(day=1).isoformat()
    raise HTTPException(400, '复盘类型须为周或月')


def date_range(scope_key):
    kind, value = scope_key.split(':', 1)
    start = date.fromisoformat(value)
    if kind == 'week' and start.weekday() == 0:
        return start, start + timedelta(days=6)
    if kind == 'month' and start.day == 1:
        return start, start.replace(day=monthrange(start.year, start.month)[1])
    raise HTTPException(400, '复盘时间范围无效')


def label(scope_key):
    start, end = date_range(scope_key)
    return f'{start} 至 {end} · ' + ('每周复盘' if scope_key.startswith('week:') else '每月复盘')


def scheduled_scopes(current_time, config):
    # Offer the latest completed calendar periods after the chosen daily time.
    # Startup later in the week/month catches up; enqueue's source hash deduplicates.
    if current_time.strftime('%H:%M') < config.get('daily_time', '21:00'):
        return []
    day = current_time.date()
    result = []
    if config.get('weekly_enabled'):
        result.append(scope('week', day - timedelta(days=day.weekday() + 1)))
    if config.get('monthly_enabled'):
        result.append(scope('month', day.replace(day=1) - timedelta(days=1)))
    return result
