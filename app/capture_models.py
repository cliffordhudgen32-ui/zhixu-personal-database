"""Unsubmitted capture text is durable, but is not a searchable entry."""
from datetime import date

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models import Identity


class CaptureDraft(Identity, Base):
    __tablename__ = 'capture_drafts'

    date: Mapped[date] = mapped_column(Date, index=True)
    title: Mapped[str] = mapped_column(String(500), default='')
    content: Mapped[str] = mapped_column(Text, default='')
    template_key: Mapped[str] = mapped_column(String(50), default='quick')
    privacy_level: Mapped[int] = mapped_column(default=1)
    project_ids: Mapped[list] = mapped_column(JSON, default=list)
    revision: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(String(20), default='open', index=True)
    discarded_at: Mapped[str | None] = mapped_column(String(40))
    submitted_at: Mapped[str | None] = mapped_column(String(40))
    submitted_entry_uuid: Mapped[str | None] = mapped_column(
        ForeignKey('entries.uuid', ondelete='SET NULL'), index=True)

    __table_args__ = (
        CheckConstraint("status IN ('open', 'discarded', 'submitted')", name='ck_capture_draft_status'),
        CheckConstraint('revision >= 1', name='ck_capture_draft_revision'),
        CheckConstraint('privacy_level BETWEEN 1 AND 3', name='ck_capture_draft_privacy'),
        Index('ix_capture_drafts_status_updated', 'status', 'updated_at', 'id'),
    )
