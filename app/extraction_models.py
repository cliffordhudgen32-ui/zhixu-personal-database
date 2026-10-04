"""Derived local attachment text; the original attachment is never rewritten."""
from sqlalchemy import Boolean, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models import now


class AttachmentExtraction(Base):
    __tablename__ = 'attachment_extractions'

    attachment_uuid: Mapped[str] = mapped_column(String(36), ForeignKey('attachments.uuid', ondelete='CASCADE'), primary_key=True)
    source_sha256: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(20), default='pending', index=True)
    text: Mapped[str] = mapped_column(Text, default='')
    pages: Mapped[list] = mapped_column(JSON, default=list)
    tool: Mapped[str] = mapped_column(String(100), default='')
    error: Mapped[str] = mapped_column(Text, default='')
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    parser_version: Mapped[str] = mapped_column(String(30), default='local-v1')
    job_token: Mapped[str] = mapped_column(String(36), default='')
    created_at: Mapped[str] = mapped_column(String(40), default=now)
    updated_at: Mapped[str] = mapped_column(String(40), default=now, onupdate=now)
