import uuid as uuidlib
from datetime import datetime, timezone, date
from sqlalchemy import Column, Integer, String, Text, Boolean, Date, ForeignKey, Table, JSON, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base

def now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')

class Identity:
    id: Mapped[int] = mapped_column(primary_key=True)
    uuid: Mapped[str] = mapped_column(String(36), unique=True, index=True, default=lambda: str(uuidlib.uuid4()))
    created_at: Mapped[str] = mapped_column(String(40), default=now, index=True)
    updated_at: Mapped[str] = mapped_column(String(40), default=now, onupdate=now)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    origin: Mapped[str] = mapped_column(String(30), default='manual', index=True)

def association(name, target):
    return Table(name, Base.metadata,
        Column('entry_id', ForeignKey('entries.id', ondelete='CASCADE'), primary_key=True),
        Column(target + '_id', ForeignKey(target + 's.id', ondelete='CASCADE'), primary_key=True),
        Index('ix_' + name + '_reverse', target + '_id', 'entry_id'))

entry_tags = association('entry_tags', 'tag')
entry_projects = association('entry_projects', 'project')
entry_people = Table('entry_people', Base.metadata,
    Column('entry_id', ForeignKey('entries.id', ondelete='CASCADE'), primary_key=True),
    Column('person_id', ForeignKey('people.id', ondelete='CASCADE'), primary_key=True),
    Index('ix_entry_people_reverse', 'person_id', 'entry_id'))

class DailyEntry(Identity, Base):
    __tablename__ = 'entries'
    item_type: Mapped[str] = mapped_column(String(50), default='entries', index=True)
    origin: Mapped[str] = mapped_column(String(30), default='manual', index=True)
    content_nature: Mapped[str] = mapped_column(String(30), default='unknown', index=True)
    original_content: Mapped[str] = mapped_column(Text, default='')
    normalized_content: Mapped[str] = mapped_column(Text, default='')
    ai_summary: Mapped[str] = mapped_column(Text, default='')
    generated_by_ai: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_provider: Mapped[str] = mapped_column(String(100), default='')
    ai_model: Mapped[str] = mapped_column(String(100), default='')
    generated_at: Mapped[str | None] = mapped_column(String(40))
    date: Mapped[date] = mapped_column(Date, index=True, default=date.today)
    title: Mapped[str] = mapped_column(String(500))
    content: Mapped[str] = mapped_column(Text, default='')
    summary: Mapped[str] = mapped_column(Text, default='')
    category: Mapped[str] = mapped_column(String(100), default='工作记录', index=True)
    sub_category: Mapped[str] = mapped_column(String(100), default='')
    importance: Mapped[int] = mapped_column(default=3, index=True)
    status: Mapped[str] = mapped_column(String(50), default='有效', index=True)
    source: Mapped[str] = mapped_column(Text, default='')
    location_text: Mapped[str] = mapped_column(String(500), default='')
    notes: Mapped[str] = mapped_column(Text, default='')
    privacy_level: Mapped[int] = mapped_column(default=1, index=True)
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    deleted_at: Mapped[str | None] = mapped_column(String(40), index=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True, default='')
    tags = relationship('Tag', secondary=entry_tags, lazy='selectin')
    projects = relationship('Project', secondary=entry_projects, lazy='selectin')
    people = relationship('Person', secondary=entry_people, lazy='selectin')
    attachments = relationship('Attachment', cascade='all, delete-orphan', passive_deletes=True, lazy='selectin')
    links = relationship('Link', cascade='all, delete-orphan', passive_deletes=True, lazy='selectin')
    __table_args__ = (Index('ix_entries_active_date', 'deleted_at', 'date', 'id'),)

class Tag(Identity, Base):
    __tablename__ = 'tags'
    name: Mapped[str] = mapped_column(String(100), unique=True)
    color: Mapped[str] = mapped_column(String(30), default='#409a84')

class Project(Identity, Base):
    __tablename__ = 'projects'
    name: Mapped[str] = mapped_column(String(300), index=True)
    description: Mapped[str] = mapped_column(Text, default='')
    status: Mapped[str] = mapped_column(String(50), default='进行中', index=True)
    priority: Mapped[int] = mapped_column(default=3)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    summary: Mapped[str] = mapped_column(Text, default='')
    privacy_level: Mapped[int] = mapped_column(default=1)

class Person(Identity, Base):
    __tablename__ = 'people'
    name: Mapped[str] = mapped_column(String(200), index=True)
    nickname: Mapped[str] = mapped_column(String(200), default='')
    organization: Mapped[str] = mapped_column(String(300), default='')
    position: Mapped[str] = mapped_column(String(300), default='')
    contact_note: Mapped[str] = mapped_column(Text, default='')
    notes: Mapped[str] = mapped_column(Text, default='')
    privacy_level: Mapped[int] = mapped_column(default=2)

class Task(Identity, Base):
    __tablename__ = 'tasks'
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text, default='')
    status: Mapped[str] = mapped_column(String(50), default='待办', index=True)
    priority: Mapped[int] = mapped_column(default=3)
    due_date: Mapped[date | None] = mapped_column(Date, index=True)
    completed_at: Mapped[str | None] = mapped_column(String(40), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey('projects.id', ondelete='SET NULL'), index=True)
    person_id: Mapped[int | None] = mapped_column(ForeignKey('people.id', ondelete='SET NULL'), index=True)
    privacy_level: Mapped[int] = mapped_column(default=1)

class Event(Identity, Base):
    __tablename__ = 'events'
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text, default='')
    date: Mapped[date] = mapped_column(Date, index=True, default=date.today)
    project_id: Mapped[int | None] = mapped_column(ForeignKey('projects.id', ondelete='SET NULL'), index=True)
    person_id: Mapped[int | None] = mapped_column(ForeignKey('people.id', ondelete='SET NULL'), index=True)
    privacy_level: Mapped[int] = mapped_column(default=1)

class Attachment(Identity, Base):
    __tablename__ = 'attachments'
    original_filename: Mapped[str] = mapped_column(String(500))
    stored_filename: Mapped[str] = mapped_column(String(100))
    file_path: Mapped[str] = mapped_column(String(500), unique=True)
    file_type: Mapped[str] = mapped_column(String(200), default='application/octet-stream')
    file_size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey('entries.id', ondelete='CASCADE'), index=True)
    description: Mapped[str] = mapped_column(Text, default='')

class Link(Identity, Base):
    __tablename__ = 'links'
    title: Mapped[str] = mapped_column(String(500), default='')
    url: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default='')
    entry_id: Mapped[int] = mapped_column(ForeignKey('entries.id', ondelete='CASCADE'), index=True)

class CustomField(Identity, Base):
    __tablename__ = 'custom_fields'
    name: Mapped[str] = mapped_column(String(100), unique=True)
    label: Mapped[str] = mapped_column(String(200))
    field_type: Mapped[str] = mapped_column(String(30), default='text')
    entity_type: Mapped[str] = mapped_column(String(40), default='entries')

class AIConversation(Identity, Base):
    __tablename__ = 'ai_conversations'
    entry_uuid: Mapped[str | None] = mapped_column(ForeignKey('entries.uuid', ondelete='CASCADE'), unique=True, index=True)
    platform: Mapped[str] = mapped_column(String(100))
    conversation_title: Mapped[str] = mapped_column(String(500))
    conversation_date: Mapped[date] = mapped_column(Date, default=date.today)
    user_message: Mapped[str] = mapped_column(Text, default='')
    assistant_message: Mapped[str] = mapped_column(Text, default='')
    summary: Mapped[str] = mapped_column(Text, default='')
    tags: Mapped[list] = mapped_column(JSON, default=list)
    project_id: Mapped[int | None] = mapped_column(ForeignKey('projects.id', ondelete='SET NULL'), index=True)
    source_file: Mapped[str] = mapped_column(Text, default='')
    privacy_level: Mapped[int] = mapped_column(default=2)

class Review(Identity, Base):
    __tablename__ = 'reviews'
    date: Mapped[date] = mapped_column(Date, index=True)
    period: Mapped[str] = mapped_column(String(20), default='day')
    summary: Mapped[str] = mapped_column(Text, default='')
    learning: Mapped[str] = mapped_column(Text, default='')
    problems: Mapped[str] = mapped_column(Text, default='')
    plan: Mapped[str] = mapped_column(Text, default='')
    review_type: Mapped[str] = mapped_column(String(50), default='day')
    target_type: Mapped[str] = mapped_column(String(50), default='')
    target_uuid: Mapped[str | None] = mapped_column(ForeignKey('entries.uuid', ondelete='SET NULL'), index=True)
    project_uuid: Mapped[str | None] = mapped_column(ForeignKey('projects.uuid', ondelete='SET NULL'), index=True)
    what_went_well: Mapped[str] = mapped_column(Text, default='')
    what_went_wrong: Mapped[str] = mapped_column(Text, default='')
    lessons: Mapped[str] = mapped_column(Text, default='')
    next_action: Mapped[str] = mapped_column(Text, default='')
    reasoning: Mapped[str] = mapped_column(Text, default='')
    expectations: Mapped[str] = mapped_column(Text, default='')
    chance_factors: Mapped[str] = mapped_column(Text, default='')
    reconsideration: Mapped[str] = mapped_column(Text, default='')
    keep_methods: Mapped[str] = mapped_column(Text, default='')
    avoid_methods: Mapped[str] = mapped_column(Text, default='')

class AuditLog(Base):
    __tablename__ = 'audit_logs'
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[str] = mapped_column(String(40), default=now, index=True)
    action: Mapped[str] = mapped_column(String(60))
    entity: Mapped[str] = mapped_column(String(60))
    object_uuid: Mapped[str] = mapped_column(String(100), default='')
    description: Mapped[str] = mapped_column(String(500), default='')

ENTITIES = {'entries': DailyEntry, 'tags': Tag, 'projects': Project, 'people': Person,
            'tasks': Task, 'events': Event, 'custom-fields': CustomField,
            'conversations': AIConversation, 'reviews': Review}
