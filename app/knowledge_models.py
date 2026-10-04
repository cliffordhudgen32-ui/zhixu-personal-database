"""Typed knowledge assets reuse DailyEntry as the common item envelope.

The shared UUID is also a foreign key: relationships cannot reference phantom items.
Common content, tags, privacy, projects, people, links and files live on the envelope;
domain-specific fields are real typed columns, not opaque JSON blobs.
"""
from datetime import date
from sqlalchemy import ForeignKey, String, Text, Date, Boolean, Float, Index, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base
from app.models import Identity, ENTITIES

class Domain(Identity, Base):
    __tablename__ = 'domains'
    name: Mapped[str] = mapped_column(String(200), index=True)
    description: Mapped[str] = mapped_column(Text, default='')
    parent_id: Mapped[int | None] = mapped_column(ForeignKey('domains.id', ondelete='RESTRICT'), index=True)

class Topic(Identity, Base):
    __tablename__ = 'topics'
    name: Mapped[str] = mapped_column(String(200), index=True)
    domain_id: Mapped[int | None] = mapped_column(ForeignKey('domains.id', ondelete='SET NULL'), index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey('topics.id', ondelete='RESTRICT'), index=True)

class Asset:
    id: Mapped[int] = mapped_column(primary_key=True)
    uuid: Mapped[str] = mapped_column(ForeignKey('entries.uuid', ondelete='CASCADE'), unique=True, index=True)
    domain_id: Mapped[int | None] = mapped_column(ForeignKey('domains.id', ondelete='SET NULL'), index=True)
    topic_id: Mapped[int | None] = mapped_column(ForeignKey('topics.id', ondelete='SET NULL'), index=True)
    times_used: Mapped[int] = mapped_column(default=0, index=True)
    last_used_at: Mapped[str | None] = mapped_column(String(40))
    reuse_score: Mapped[float] = mapped_column(Float, default=0)

class Knowledge(Asset, Base):
    __tablename__ = 'knowledge'
    knowledge_type: Mapped[str] = mapped_column(String(100), default='其他知识', index=True)
    difficulty: Mapped[int] = mapped_column(default=1)
    source_type: Mapped[str] = mapped_column(String(100), default='自己实践')
    source_url: Mapped[str] = mapped_column(Text, default='')
    source_title: Mapped[str] = mapped_column(String(500), default='')
    author: Mapped[str] = mapped_column(String(300), default='')
    published_date: Mapped[date | None] = mapped_column(Date)
    learned_date: Mapped[date | None] = mapped_column(Date)
    confidence: Mapped[str] = mapped_column(String(30), default='unknown', index=True)
    maturity_level: Mapped[int] = mapped_column(default=1, index=True)
    last_reviewed_at: Mapped[str | None] = mapped_column(String(40))
    next_review_date: Mapped[date | None] = mapped_column(Date, index=True)
    review_count: Mapped[int] = mapped_column(default=0)
    needs_verification: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (CheckConstraint('maturity_level >= 1 AND maturity_level <= 6'),)

class Case(Asset, Base):
    __tablename__ = 'cases'
    case_type: Mapped[str] = mapped_column(String(100), default='问题解决案例', index=True)
    background: Mapped[str] = mapped_column(Text, default='')
    problem: Mapped[str] = mapped_column(Text, default='')
    goal: Mapped[str] = mapped_column(Text, default='')
    constraints: Mapped[str] = mapped_column(Text, default='')
    analysis: Mapped[str] = mapped_column(Text, default='')
    solution: Mapped[str] = mapped_column(Text, default='')
    execution: Mapped[str] = mapped_column(Text, default='')
    result: Mapped[str] = mapped_column(Text, default='')
    outcome: Mapped[str] = mapped_column(String(30), default='未知', index=True)
    lessons: Mapped[str] = mapped_column(Text, default='')
    mistakes: Mapped[str] = mapped_column(Text, default='')
    success_factors: Mapped[str] = mapped_column(Text, default='')
    failure_factors: Mapped[str] = mapped_column(Text, default='')
    reusable_method: Mapped[str] = mapped_column(Text, default='')
    applicable_conditions: Mapped[str] = mapped_column(Text, default='')
    not_applicable_conditions: Mapped[str] = mapped_column(Text, default='')
    future_improvements: Mapped[str] = mapped_column(Text, default='')
    start_date: Mapped[date | None] = mapped_column(Date, index=True)
    end_date: Mapped[date | None] = mapped_column(Date)
    is_reviewed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

class Solution(Asset, Base):
    __tablename__ = 'solutions'
    problem_type: Mapped[str] = mapped_column(String(200), default='', index=True)
    description: Mapped[str] = mapped_column(Text, default='')
    steps: Mapped[str] = mapped_column(Text, default='')
    requirements: Mapped[str] = mapped_column(Text, default='')
    advantages: Mapped[str] = mapped_column(Text, default='')
    disadvantages: Mapped[str] = mapped_column(Text, default='')
    risks: Mapped[str] = mapped_column(Text, default='')
    cost: Mapped[str] = mapped_column(String(300), default='')
    difficulty: Mapped[int] = mapped_column(default=1)
    success_rate_note: Mapped[str] = mapped_column(Text, default='')
    applicable_conditions: Mapped[str] = mapped_column(Text, default='')
    not_applicable_conditions: Mapped[str] = mapped_column(Text, default='')
    verification_status: Mapped[str] = mapped_column(String(50), default='未验证', index=True)
    confidence: Mapped[str] = mapped_column(String(30), default='unknown', index=True)

class Problem(Asset, Base):
    __tablename__ = 'problems'
    occurred_date: Mapped[date | None] = mapped_column(Date, index=True)
    impact: Mapped[str] = mapped_column(Text, default='')
    possible_causes: Mapped[str] = mapped_column(Text, default='')
    root_cause: Mapped[str] = mapped_column(Text, default='')
    final_solution: Mapped[str] = mapped_column(Text, default='')
    execution: Mapped[str] = mapped_column(Text, default='')
    result: Mapped[str] = mapped_column(Text, default='')
    is_resolved: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    recurrence_count: Mapped[int] = mapped_column(default=0)
    last_recurred_date: Mapped[date | None] = mapped_column(Date)

class Experience(Asset, Base):
    __tablename__ = 'experiences'
    context: Mapped[str] = mapped_column(Text, default='')
    source_case_uuid: Mapped[str | None] = mapped_column(ForeignKey('cases.uuid', ondelete='SET NULL'), index=True)
    confidence: Mapped[str] = mapped_column(String(30), default='unknown', index=True)
    times_verified: Mapped[int] = mapped_column(default=0)
    last_verified_at: Mapped[str | None] = mapped_column(String(40))

class Source(Asset, Base):
    __tablename__ = 'sources'
    source_type: Mapped[str] = mapped_column(String(100), default='网页', index=True)
    author: Mapped[str] = mapped_column(String(300), default='')
    organization: Mapped[str] = mapped_column(String(300), default='')
    url: Mapped[str] = mapped_column(Text, default='')
    publication_date: Mapped[date | None] = mapped_column(Date)
    access_date: Mapped[date | None] = mapped_column(Date)
    description: Mapped[str] = mapped_column(Text, default='')
    file_id: Mapped[int | None] = mapped_column(ForeignKey('attachments.id', ondelete='SET NULL'), index=True)

class LearningRecord(Asset, Base):
    __tablename__ = 'learning_records'
    learning_type: Mapped[str] = mapped_column(String(100), default='文章', index=True)
    source: Mapped[str] = mapped_column(Text, default='')
    start_date: Mapped[date | None] = mapped_column(Date)
    finish_date: Mapped[date | None] = mapped_column(Date)
    progress: Mapped[int] = mapped_column(default=0)
    notes: Mapped[str] = mapped_column(Text, default='')
    key_points: Mapped[str] = mapped_column(Text, default='')
    questions: Mapped[str] = mapped_column(Text, default='')
    application: Mapped[str] = mapped_column(Text, default='')
    review_date: Mapped[date | None] = mapped_column(Date, index=True)
    maturity_level: Mapped[int] = mapped_column(default=1, index=True)

class KnowledgeRelation(Identity, Base):
    __tablename__ = 'knowledge_relations'
    from_uuid: Mapped[str] = mapped_column(ForeignKey('entries.uuid', ondelete='CASCADE'), index=True)
    to_uuid: Mapped[str] = mapped_column(ForeignKey('entries.uuid', ondelete='CASCADE'), index=True)
    relation_type: Mapped[str] = mapped_column(String(60), default='related_to', index=True)
    description: Mapped[str] = mapped_column(Text, default='')
    judgment: Mapped[str] = mapped_column(Text, default='')
    evidence: Mapped[str] = mapped_column(Text, default='')

class Revision(Identity, Base):
    __tablename__ = 'revisions'
    item_uuid: Mapped[str] = mapped_column(ForeignKey('entries.uuid', ondelete='CASCADE'), index=True)
    reason: Mapped[str] = mapped_column(Text, default='编辑更新')
    old_version: Mapped[dict] = mapped_column(__import__('sqlalchemy').JSON)
    new_version: Mapped[dict] = mapped_column(__import__('sqlalchemy').JSON)

class ReuseLog(Identity, Base):
    __tablename__ = 'reuse_logs'
    item_uuid: Mapped[str] = mapped_column(ForeignKey('entries.uuid', ondelete='CASCADE'), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey('projects.id', ondelete='SET NULL'), index=True)
    used_at: Mapped[str] = mapped_column(String(40), index=True)
    result: Mapped[str] = mapped_column(Text, default='')
    notes: Mapped[str] = mapped_column(Text, default='')

class CaseTask(Base):
    __tablename__ = 'case_tasks'
    case_uuid: Mapped[str] = mapped_column(ForeignKey('cases.uuid', ondelete='CASCADE'), primary_key=True)
    task_uuid: Mapped[str] = mapped_column(ForeignKey('tasks.uuid', ondelete='CASCADE'), primary_key=True)
    __table_args__ = (Index('ix_case_tasks_reverse','task_uuid','case_uuid'),)

class CaseEvent(Base):
    __tablename__ = 'case_events'
    case_uuid: Mapped[str] = mapped_column(ForeignKey('cases.uuid', ondelete='CASCADE'), primary_key=True)
    event_uuid: Mapped[str] = mapped_column(ForeignKey('events.uuid', ondelete='CASCADE'), primary_key=True)
    __table_args__ = (Index('ix_case_events_reverse','event_uuid','case_uuid'),)

ASSETS = {'knowledge': Knowledge, 'cases': Case, 'solutions': Solution, 'problems': Problem,
          'experiences': Experience, 'sources': Source, 'learning': LearningRecord}
ENTITIES.update({'domains':Domain, 'topics':Topic, 'relations':KnowledgeRelation})
