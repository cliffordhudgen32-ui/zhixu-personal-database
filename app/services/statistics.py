from datetime import date, timedelta, datetime, timezone
from sqlalchemy import select, func
from app.models import DailyEntry, Task, Project, Person, Tag, Event, entry_tags, entry_projects, entry_people
from app.services.records import serialize

def statistics(session, start=None, end=None):
    today = date.today()
    def entry_count(a, b):
        return session.scalar(select(func.count(DailyEntry.id)).where(DailyEntry.deleted_at.is_(None), DailyEntry.date >= a, DailyEntry.date <= b))
    conditions = [DailyEntry.deleted_at.is_(None)]
    if start:
        conditions.append(DailyEntry.date >= start)
    if end:
        conditions.append(DailyEntry.date <= end)
    def groups(expr):
        return [{'label': str(k), 'count': n} for k, n in session.execute(select(expr, func.count()).where(*conditions).group_by(expr).order_by(expr))]
    def related_counts(model, relation, key):
        q = select(model.name, func.count()).join(relation, relation.c[key] == model.id).join(DailyEntry, DailyEntry.id == relation.c.entry_id).where(*conditions).group_by(model.id).order_by(func.count().desc()).limit(20)
        return [{'label': k, 'count': n} for k, n in session.execute(q)]
    task_q = select(func.count(Task.id)).where(Task.completed_at.is_not(None))
    if start:
        task_q = task_q.where(Task.completed_at >= datetime.combine(start, datetime.min.time()).astimezone().astimezone(timezone.utc).isoformat())
    if end:
        task_q = task_q.where(Task.completed_at < datetime.combine(end + timedelta(days=1), datetime.min.time()).astimezone().astimezone(timezone.utc).isoformat())
    events = select(Event).order_by(Event.date.desc()).limit(50)
    if start:
        events = events.where(Event.date >= start)
    if end:
        events = events.where(Event.date <= end)
    person_ids=select(entry_people.c.person_id).join(DailyEntry,DailyEntry.id==entry_people.c.entry_id).where(*conditions).distinct()
    project_ids=select(entry_projects.c.project_id).join(DailyEntry,DailyEntry.id==entry_projects.c.entry_id).where(*conditions).distinct()
    return dict(today=today.isoformat(), today_count=entry_count(today,today),
        week_count=entry_count(today - timedelta(days=today.weekday()), today),
        month_count=entry_count(today.replace(day=1), today),
        total=session.scalar(select(func.count(DailyEntry.id)).where(*conditions)),
        people_count=session.scalar(select(func.count()).select_from(person_ids.subquery())),
        project_count=session.scalar(select(func.count()).select_from(project_ids.subquery())),
        categories=groups(DailyEntry.category), daily=groups(DailyEntry.date),
        monthly=groups(func.substr(DailyEntry.date, 1, 7)),
        tags=related_counts(Tag, entry_tags, 'tag_id'), projects=related_counts(Project, entry_projects, 'project_id'),
        completed_tasks=session.scalar(task_q),
        open_tasks=[serialize(t) for t in session.scalars(select(Task).where(Task.status != '已完成').order_by(Task.due_date.asc().nulls_last(), Task.priority.desc()).limit(20))],
        today_tasks=[serialize(t) for t in session.scalars(select(Task).where(Task.due_date == today, Task.status != '已完成').limit(50))],
        people=[{'name': p.name, 'uuid': p.uuid} for p in session.scalars(select(Person).where(Person.id.in_(person_ids)).limit(50))],
        events=[serialize(e) for e in session.scalars(events)])
