"""Rebuildable local vector chunks; original entries and files remain authoritative."""
from sqlalchemy import ForeignKey, Index, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models import now


class EmbeddingChunk(Base):
    __tablename__ = 'embedding_chunks'

    id: Mapped[int] = mapped_column(primary_key=True)
    entry_uuid: Mapped[str] = mapped_column(String(36), ForeignKey('entries.uuid', ondelete='CASCADE'), index=True)
    model: Mapped[str] = mapped_column(String(200))
    model_version: Mapped[str] = mapped_column(String(200), index=True)
    source_hash: Mapped[str] = mapped_column(String(64), index=True)
    entry_updated_at: Mapped[str] = mapped_column(String(40))
    chunk_index: Mapped[int] = mapped_column()
    field: Mapped[str] = mapped_column(String(100))
    attachment_uuid: Mapped[str | None] = mapped_column(String(36), ForeignKey('attachments.uuid', ondelete='CASCADE'), index=True)
    attachment_sha256: Mapped[str | None] = mapped_column(String(64))
    page: Mapped[int | None] = mapped_column()
    source_label: Mapped[str] = mapped_column(String(500), default='')
    start_offset: Mapped[int] = mapped_column()
    end_offset: Mapped[int] = mapped_column()
    evidence: Mapped[str] = mapped_column(Text)
    dimension: Mapped[int] = mapped_column()
    vector: Mapped[bytes] = mapped_column(LargeBinary)  # Little-endian, normalized float32.
    created_at: Mapped[str] = mapped_column(String(40), default=now)

    __table_args__ = (
        UniqueConstraint('entry_uuid', 'model_version', 'chunk_index', name='uq_embedding_chunk_position'),
        Index('ix_embedding_chunk_model_entry', 'model_version', 'entry_uuid'),
    )
