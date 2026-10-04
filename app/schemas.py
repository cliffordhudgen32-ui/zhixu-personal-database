from datetime import date as DateValue
from typing import Any
from uuid import UUID
from pydantic import BaseModel, Field, ConfigDict, create_model, model_validator
from sqlalchemy import Date, Integer, Boolean, JSON
from app.models import ENTITIES
from app.knowledge_models import ASSETS

class InputBase(BaseModel):
    model_config = ConfigDict(extra='forbid')

class EntryInput(InputBase):
    uuid: UUID | None = None
    date: DateValue = Field(default_factory=DateValue.today)
    title: str = Field(min_length=1, max_length=500)
    content: str = Field(default='', max_length=2_000_000)
    summary: str = ''
    category: str = Field(default='工作记录', min_length=1, max_length=100)
    sub_category: str = ''
    importance: int = Field(default=3, ge=1, le=5)
    status: str = '有效'
    source: str = ''
    location_text: str = ''
    notes: str = ''
    privacy_level: int = Field(default=1, ge=1, le=3)
    is_favorite: bool = False
    is_archived: bool = False
    metadata_json: dict = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list, max_length=100)
    projects: list[UUID] = Field(default_factory=list, max_length=100)
    people: list[UUID] = Field(default_factory=list, max_length=100)
    item_type: str = 'entries'
    origin: str = 'manual'
    content_nature: str = 'unknown'
    normalized_content: str = ''
    ai_summary: str = ''
    generated_by_ai: bool = False
    ai_provider: str = ''
    ai_model: str = ''
    generated_at: str | None = None

    @model_validator(mode='after')
    def valid_classification(self):
        if self.item_type not in ('entries', 'inbox', 'conversations', *ASSETS):
            raise ValueError('未知内容类型')
        if self.content_nature not in ('fact','opinion','hypothesis','experience','reference','ai_generated','unknown'):
            raise ValueError('信息性质无效')
        if self.origin not in ('manual','import','ai','web','file','conversation','api','migration'):
            raise ValueError('创建来源无效')
        return self

class AssetInput(EntryInput):
    details: dict = Field(default_factory=dict)
    revision_reason: str = '编辑更新'

SCHEMAS = {'entries': EntryInput}
for name, model in ENTITIES.items():
    if name == 'entries':
        continue
    fields: dict[str, Any] = {}
    for col in model.__table__.columns:
        if col.name in ('id', 'created_at', 'updated_at', 'completed_at', 'entry_uuid'):
            continue
        if col.name == 'uuid':
            fields[col.name] = (UUID | None, None)
            continue
        typ = DateValue if isinstance(col.type, Date) else bool if isinstance(col.type, Boolean) else int if isinstance(col.type, Integer) else (list if col.name == 'tags' else dict) if isinstance(col.type, JSON) else str
        default = None if col.nullable else col.default.arg if col.default is not None and col.default.is_scalar else {} if typ is dict else [] if typ is list else ...
        if isinstance(col.type, Date) and col.default is not None:
            default = Field(default_factory=DateValue.today)
        if col.nullable:
            typ = typ | None
        if col.name in ('priority', 'privacy_level'):
            default = Field(default=default, ge=1, le=3 if col.name == 'privacy_level' else 5)
        elif typ is str:
            default = Field(default=default, min_length=1 if col.name in ('name', 'title', 'label') else 0, max_length=col.type.length or 2_000_000)
        fields[col.name] = (typ, default)
    SCHEMAS[name] = create_model(model.__name__ + 'Input', __base__=InputBase, **fields)

class SettingsInput(InputBase):
    name: str = Field(default='个人数字记忆', min_length=1, max_length=100)
    nickname: str = Field(default='', max_length=100)
    attachment_dir: str = 'data/attachments'
    backup_dir: str = 'backups'
    auto_backup: int = 1
    retention: int = 30
    theme: str = 'system'
    date_format: str = 'YYYY-MM-DD'
    setup_done: bool = True

    @model_validator(mode='after')
    def choices(self):
        if self.auto_backup not in (0, 1, 3, 7) or self.retention not in (30, 60, 100):
            raise ValueError('备份设置无效')
        if self.theme not in ('system', 'light', 'dark') or self.date_format not in ('YYYY-MM-DD', 'YYYY/MM/DD'):
            raise ValueError('显示设置无效')
        return self
