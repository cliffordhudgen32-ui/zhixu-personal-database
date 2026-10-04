from datetime import date as DateValue
from uuid import UUID
from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import Field
from sqlalchemy import select, func
from sqlalchemy.orm import load_only
from app.database import transaction
from app.schemas import InputBase
from app.workflow_models import AIDraft, AIReviewRevision, AIJob
from app.services.records import get, serialize, audit
from app.services import workflow

router = APIRouter()


class DailyRequest(InputBase):
    date: DateValue = Field(default_factory=workflow.today_local)


class PeriodRequest(InputBase):
    kind: Literal['week', 'month']
    date: DateValue = Field(default_factory=workflow.today_local)


class OrganizeRequest(InputBase):
    uuid: UUID | None = None
    entry_uuid: UUID | None = None
    operation: str = 'organize'
    date: DateValue = Field(default_factory=workflow.today_local)


class DraftEdit(InputBase):
    revision: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1, max_length=2_000_000)
    summary: str = Field(default='', max_length=100_000)
    category: str = Field(default='每日总结', min_length=1, max_length=100)
    tags: list[str] = Field(default_factory=list, max_length=100)
    answers: dict[str, str] = Field(default_factory=dict, max_length=100)


class ApproveRequest(InputBase):
    revision: int = Field(ge=1)
    confirmation: str
    acknowledge_uncertain: bool = False


class PullRequest(InputBase):
    model: str = Field(min_length=1, max_length=100)


class RewriteRequest(InputBase):
    revision: int = Field(ge=1)
    selection: str = Field(min_length=1, max_length=4000)
    instruction: str = Field(default='表达更清楚，保留所有已写明的事实与不确定信息', max_length=500)


@router.get('/api/ai/settings')
def ai_settings():
    from app.services.ai_settings import public_settings
    return public_settings()


@router.put('/api/ai/settings')
def ai_settings_save(payload: dict):
    from app.services.ai_settings import save_config
    try:
        return save_config(payload)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get('/api/ai/models')
def ai_models():
    from app.services.ai_settings import get_config
    from app.services.local_ai import models
    try:
        return models(get_config())
    except Exception as exc:
        raise HTTPException(409, '模型服务尚未准备，请检查AI设置与模型下载进度') from exc


@router.post('/api/ai/model-pull')
def ai_pull(payload: PullRequest):
    from app.services.ai_worker import submit_job
    from app.services.local_ai import validate_model
    try:
        validate_model(payload.model)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return submit_job('model_pull', {'model': payload.model})


@router.get('/api/ai/jobs')
def jobs():
    with transaction() as s:
        rows = list(s.scalars(select(AIJob).order_by(AIJob.id.desc()).limit(30)))
        return {'items': [serialize(row, False) | {'model': row.payload.get('model', '')} for row in rows]}


@router.post('/api/ai/daily')
def daily(payload: DailyRequest):
    return workflow.enqueue('daily:' + payload.date.isoformat())


@router.post('/api/ai/period')
def period(payload: PeriodRequest):
    from app.services.period_review import scope
    return workflow.enqueue(scope(payload.kind, payload.date))


@router.post('/api/ai/organize')
def organize(payload: OrganizeRequest | None = None):
    payload = payload or OrganizeRequest()
    uid = payload.entry_uuid or payload.uuid
    return workflow.enqueue('entry:' + str(uid) if uid else 'daily:' + payload.date.isoformat())


@router.get('/api/ai/drafts')
def drafts(date: DateValue | None = None, status: str = '', page: int = 1, size: int = 20, scope_kind: str = ''):
    if page < 1 or size < 1 or size > 100:
        raise HTTPException(400, '页码或分页大小无效')
    with transaction() as s:
        query = select(AIDraft)
        if scope_kind:
            if scope_kind not in ('daily', 'week', 'month', 'entry'):
                raise HTTPException(400, '草稿范围类型无效')
            query = query.where(AIDraft.scope_key.startswith(scope_kind + ':'))
        if date:
            query = query.where(AIDraft.date == date)
        counts = dict(s.execute(select(AIDraft.status, func.count()).where(AIDraft.uuid.in_(select(query.subquery().c.uuid))).group_by(AIDraft.status)).all())
        counts.setdefault('pending_review', 0)
        if status:
            query = query.where(AIDraft.status == status)
        total = s.scalar(select(func.count()).select_from(query.subquery()))
        fields = ('uuid', 'scope_key', 'date', 'status', 'title', 'summary', 'provider', 'model', 'error', 'questions', 'archived_uuid', 'revision', 'created_at', 'updated_at')
        rows = list(s.scalars(query.options(load_only(*(getattr(AIDraft, key) for key in fields)))
            .order_by(AIDraft.id.desc()).offset((page - 1) * size).limit(size)))
        items = [{key: (getattr(row, key).isoformat() if key == 'date' else getattr(row, key)) for key in fields} for row in rows]
        for item in items:
            item['summary'] = item['summary'][:1000]
        return {'items': items, 'counts': counts, 'total': total, 'page': page, 'size': size}


@router.get('/api/ai/drafts/{uid}')
def draft_detail(uid: str):
    with transaction() as s:
        return serialize(get(s, AIDraft, uid), False)


@router.get('/api/ai/drafts/{uid}/history')
def draft_history(uid: str, page: int = 1, size: int = 20):
    if page < 1 or not 1 <= size <= 100:
        raise HTTPException(400, '页码或分页大小无效')
    with transaction() as s:
        get(s, AIDraft, uid)
        query = select(AIReviewRevision).where(AIReviewRevision.draft_uuid == uid)
        total = s.scalar(select(func.count()).select_from(query.subquery()))
        return {'items': [serialize(row, False) for row in s.scalars(query.order_by(AIReviewRevision.id.desc()).offset((page - 1) * size).limit(size))], 'total': total, 'page': page, 'size': size}


@router.patch('/api/ai/drafts/{uid}')
def edit_draft(uid: str, payload: DraftEdit):
    if any(len(tag) > 100 for tag in payload.tags) or any(len(value) > 10000 for value in payload.answers.values()):
        raise HTTPException(400, '标签或补充答案过长')
    with transaction() as s:
        draft = get(s, AIDraft, uid)
        if draft.status != 'pending_review' or draft.revision != payload.revision:
            raise HTTPException(409, '草稿已变化或已归档，请刷新后编辑')
        allowed = {question['id'] for question in draft.questions}
        if any(key not in allowed for key in payload.answers):
            raise HTTPException(400, '补充答案对应的问题已变化，请刷新')
        for key, value in payload.model_dump(exclude={'revision'}).items():
            setattr(draft, key, value)
        draft.revision += 1
        workflow._review_revision(s, draft, 'edited')
        audit(s, 'ai_review_edit', 'ai_drafts', uid, '保存人工审核修改')
        s.flush()
        return serialize(draft, False)


@router.post('/api/ai/drafts/{uid}/approve')
def approve_draft(uid: str, payload: ApproveRequest):
    if payload.confirmation != '确认归档':
        raise HTTPException(400, '需要明确确认归档')
    result = workflow.approve(uid, payload.revision, payload.acknowledge_uncertain)
    try:
        from app.semantic_routes import enqueue_index
        enqueue_index(result['archived_uuid'])
    except Exception:
        pass
    return result


@router.post('/api/ai/drafts/{uid}/rewrite')
def rewrite_draft_selection(uid: str, payload: RewriteRequest):
    from app.services.review_assistance import suggest_rewrite
    return suggest_rewrite(uid, payload.revision, payload.selection, payload.instruction)


@router.post('/api/ai/drafts/{uid}/reject')
def reject_draft(uid: str):
    with transaction() as s:
        draft = get(s, AIDraft, uid)
        if draft.status != 'pending_review':
            raise HTTPException(409, '只有待审核草稿可以退回')
        draft.status, draft.revision = 'rejected', draft.revision + 1
        workflow._review_revision(s, draft, 'rejected')
        s.flush()
        return serialize(draft, False)


@router.post('/api/ai/drafts/{uid}/retry')
def retry_draft(uid: str):
    with transaction() as s:
        draft = get(s, AIDraft, uid)
        if draft.status not in ('failed', 'rejected'):
            raise HTTPException(409, '该草稿仍在处理中或已归档')
        scope = draft.scope_key
    queued = workflow.enqueue(scope)
    if queued['uuid'] != uid:
        return queued
    with transaction() as s:
        draft = get(s, AIDraft, uid)
        draft.status, draft.error = 'queued', ''
        s.flush()
        return serialize(draft, False)
