"""Durable AI drafts: source snapshots and human approval are separate from entries."""
from sqlalchemy import String, Text, JSON, Date, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base
from app.models import Identity, now
from datetime import date


class AIConfiguration(Base):
    __tablename__ = 'ai_configuration'
    id: Mapped[int] = mapped_column(primary_key=True)
    values: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[str] = mapped_column(String(40), default=now, onupdate=now)


class AIDraft(Identity, Base):
    __tablename__ = 'ai_drafts'
    date: Mapped[date] = mapped_column(Date, index=True)
    scope_key: Mapped[str] = mapped_column(String(80), index=True)
    request_key: Mapped[str] = mapped_column(String(64), unique=True)
    source_hash: Mapped[str] = mapped_column(String(64))
    target_hash: Mapped[str] = mapped_column(String(64), default='')
    status: Mapped[str] = mapped_column(String(30), default='queued', index=True)
    revision: Mapped[int] = mapped_column(default=1)
    title: Mapped[str] = mapped_column(String(500), default='')
    content: Mapped[str] = mapped_column(Text, default='')
    summary: Mapped[str] = mapped_column(Text, default='')
    category: Mapped[str] = mapped_column(String(100), default='每日总结')
    tags: Mapped[list] = mapped_column(JSON, default=list)
    questions: Mapped[list] = mapped_column(JSON, default=list)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    source_records: Mapped[list] = mapped_column(JSON, default=list)
    answers: Mapped[dict] = mapped_column(JSON, default=dict)
    provider: Mapped[str] = mapped_column(String(100), default='')
    model: Mapped[str] = mapped_column(String(100), default='')
    error: Mapped[str] = mapped_column(Text, default='')
    archived_uuid: Mapped[str | None] = mapped_column(ForeignKey('entries.uuid', ondelete='SET NULL'))
    approved_at: Mapped[str | None] = mapped_column(String(40))


class AIArchive(Base):
    __tablename__ = 'ai_archives'
    scope_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    entry_uuid: Mapped[str | None] = mapped_column(ForeignKey('entries.uuid', ondelete='SET NULL'))
    last_source_hash: Mapped[str] = mapped_column(String(64), default='')
    last_draft_uuid: Mapped[str | None] = mapped_column(ForeignKey('ai_drafts.uuid', ondelete='SET NULL'))
    updated_at: Mapped[str] = mapped_column(String(40), default=now, onupdate=now)


class AIReviewRevision(Identity, Base):
    __tablename__ = 'ai_review_revisions'
    draft_uuid: Mapped[str] = mapped_column(ForeignKey('ai_drafts.uuid', ondelete='CASCADE'), index=True)
    revision: Mapped[int] = mapped_column()
    action: Mapped[str] = mapped_column(String(30))
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)


class AIJob(Identity, Base):
    __tablename__ = 'ai_jobs'
    kind: Mapped[str] = mapped_column(String(50), index=True)
    status: Mapped[str] = mapped_column(String(30), default='queued', index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    progress: Mapped[dict] = mapped_column(JSON, default=dict)
    message: Mapped[str] = mapped_column(Text, default='')
    error: Mapped[str] = mapped_column(Text, default='')
