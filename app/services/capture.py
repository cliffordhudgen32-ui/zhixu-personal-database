"""Save recoverable text drafts and submit exactly one inbox entry per UUID."""
from datetime import date as DateValue
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field, model_validator
from sqlalchemy import func, select

from app.capture_models import CaptureDraft
from app.database import transaction
from app.models import DailyEntry, Project, now
from app.schemas import InputBase
from app.services.records import audit, get, save, serialize

DraftStatus = Literal['open', 'discarded', 'submitted']


class CaptureInput(InputBase):
    revision: int = Field(ge=0, strict=True)
    date: DateValue
    title: str = Field(default='', max_length=500)
    content: str = Field(default='', max_length=2_000_000)
    template_key: str = Field(default='quick', max_length=50)
    privacy_level: int = Field(default=1, ge=1, le=3, strict=True)
    project_ids: list[UUID] = Field(default_factory=list, max_length=100)

    @model_validator(mode='after')
    def unique_projects(self):
        self.project_ids = list(dict.fromkeys(self.project_ids))
        return self


class RevisionInput(InputBase):
    revision: int = Field(ge=1, strict=True)


def _identity(uid):
    try:
        return str(UUID(str(uid)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(422, '草稿标识格式不正确') from exc


def draft_value(draft):
    """Keep implementation metadata and database identifiers out of the API."""
    return {
        'uuid': draft.uuid, 'date': draft.date.isoformat(), 'title': draft.title,
        'content': draft.content, 'template_key': draft.template_key,
        'privacy_level': draft.privacy_level, 'project_ids': list(draft.project_ids),
        'revision': draft.revision, 'status': draft.status,
        'submitted_entry_uuid': draft.submitted_entry_uuid,
        'created_at': draft.created_at, 'updated_at': draft.updated_at,
        'discarded_at': draft.discarded_at, 'submitted_at': draft.submitted_at,
    }


def _projects(session, identities):
    ids = [str(uid) for uid in identities]
    if ids and session.scalar(select(func.count(Project.id)).where(Project.uuid.in_(ids))) != len(ids):
        raise HTTPException(400, '关联的项目已不存在，请重新选择后保存')
    return ids


def _revision(draft, revision):
    if draft.revision != revision:
        raise HTTPException(409, '草稿已在其他窗口更新，请读取最新版本后再保存；当前文字没有覆盖')


def _open(draft):
    if draft.status == 'discarded':
        raise HTTPException(409, '这份草稿已放入草稿回收站，请先恢复后继续编辑')
    if draft.status == 'submitted':
        raise HTTPException(409, '这份草稿已保存为原始记录，请打开记录继续编辑')


def get_draft(uid):
    with transaction() as session:
        return draft_value(get(session, CaptureDraft, _identity(uid)))


def list_drafts(status: DraftStatus = 'open', page=1, size=20):
    if status not in ('open', 'discarded', 'submitted') or not 1 <= size <= 50 or not 1 <= page <= 100_000:
        raise HTTPException(422, '草稿筛选或分页范围不正确')
    with transaction() as session:
        counts = dict.fromkeys(('open', 'discarded', 'submitted'), 0)
        counts.update(session.execute(select(CaptureDraft.status, func.count(CaptureDraft.id))
                                      .group_by(CaptureDraft.status)).all())
        # List pages never load a two-million-character body for each card.
        names = ('uuid', 'date', 'title', 'template_key', 'privacy_level', 'project_ids',
                 'revision', 'status', 'submitted_entry_uuid', 'created_at', 'updated_at',
                 'discarded_at', 'submitted_at')
        query = select(*(getattr(CaptureDraft, name) for name in names),
                       func.substr(CaptureDraft.content, 1, 280).label('content_preview'),
                       func.length(CaptureDraft.content).label('content_length'))
        rows = session.execute(query.where(CaptureDraft.status == status)
                               .order_by(CaptureDraft.updated_at.desc(), CaptureDraft.id.desc())
                               .offset((page - 1) * size).limit(size))
        items = []
        for row in rows:
            value = dict(row._mapping)
            value['date'] = value['date'].isoformat()
            items.append(value)
        return {'items': items, 'total': counts[status], 'page': page, 'size': size, 'counts': counts}


def save_draft(uid, payload: CaptureInput):
    identity = _identity(uid)
    with transaction() as session:
        draft = session.scalar(select(CaptureDraft).where(CaptureDraft.uuid == identity))
        if draft is None:
            if payload.revision != 0:
                raise HTTPException(409, '草稿已不存在，请使用新的草稿标识另存当前文字')
            if session.scalar(select(DailyEntry.id).where(DailyEntry.uuid == identity)):
                raise HTTPException(409, '这份草稿标识已用于原始记录，请使用新的草稿标识')
            draft = CaptureDraft(uuid=identity, revision=1, status='open', origin='manual')
        else:
            _open(draft)
            _revision(draft, payload.revision)
            draft.revision += 1
        values = payload.model_dump(exclude={'revision', 'project_ids'})
        values['project_ids'] = _projects(session, payload.project_ids)
        for name, value in values.items():
            setattr(draft, name, value)
        draft.updated_at = now()
        session.add(draft)
        session.flush()
        # Automatic keystroke saves intentionally do not create audit rows or log prose.
        return draft_value(draft)


def submit_draft(uid, revision):
    identity = _identity(uid)
    with transaction() as session:
        draft = get(session, CaptureDraft, identity)
        if draft.status == 'submitted':
            entry = session.scalar(select(DailyEntry).where(DailyEntry.uuid == draft.submitted_entry_uuid))
            if entry is None:
                raise HTTPException(410, '这份草稿对应的原始记录已被彻底删除；没有重新创建记录')
            return {'draft': draft_value(draft), 'entry': serialize(entry), 'reused': True}
        _open(draft)
        _revision(draft, revision)
        if not draft.content.strip():
            raise HTTPException(400, '请填写正文并核对内容后再保存到收集箱')
        if session.scalar(select(DailyEntry.id).where(DailyEntry.uuid == identity)):
            raise HTTPException(409, '同一标识已用于原始记录，草稿没有覆盖该记录')
        projects = _projects(session, draft.project_ids)
        title = draft.title.strip() or next((line.strip()[:500] for line in draft.content.splitlines()
                                           if line.strip()), '随手记录')
        entry = save(session, 'entries', {
            'uuid': identity, 'date': draft.date, 'title': title,
            'content': draft.content, 'item_type': 'inbox', 'origin': 'manual',
            'category': '快速收集', 'privacy_level': draft.privacy_level, 'projects': projects,
            'metadata_json': {'capture_draft_uuid': identity, 'capture_template': draft.template_key},
        })
        draft.status = 'submitted'
        draft.revision += 1
        draft.submitted_at = draft.updated_at = now()
        draft.submitted_entry_uuid = entry.uuid
        session.flush()
        audit(session, '提交文字草稿', 'capture_drafts', identity)
        return {'draft': draft_value(draft), 'entry': serialize(entry), 'reused': False}


def _change_status(uid, revision, *, restore):
    with transaction() as session:
        draft = get(session, CaptureDraft, _identity(uid))
        _revision(draft, revision)
        expected = 'discarded' if restore else 'open'
        if draft.status != expected:
            raise HTTPException(409, '草稿状态已改变，请重新打开草稿列表')
        draft.status = 'open' if restore else 'discarded'
        draft.revision += 1
        draft.updated_at = now()
        draft.discarded_at = None if restore else draft.updated_at
        session.flush()
        audit(session, '恢复文字草稿' if restore else '丢弃文字草稿', 'capture_drafts', draft.uuid)
        return draft_value(draft)


def discard_draft(uid, revision):
    return _change_status(uid, revision, restore=False)


def restore_draft(uid, revision):
    return _change_status(uid, revision, restore=True)
