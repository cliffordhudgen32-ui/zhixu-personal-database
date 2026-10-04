"""Semantic API; model preparation and indexing are queued, never request-bound."""
from concurrent.futures import ThreadPoolExecutor
from threading import RLock
import os
import time
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request

from app.services import semantic

router = APIRouter()
_SUBMITTER = None
_EXECUTOR = None
_JOBS = {}
_JOB_LOCK = RLock()
_DEFERRED_ALL = False


def configure_submitter(submitter):
    """Root workflow can inject submitter(kind, payload) to share its durable queue."""
    global _SUBMITTER
    _SUBMITTER = submitter


configure_semantic_submitter = configure_submitter


def _local_submit(kind, payload):
    global _EXECUTOR
    if os.getenv('PLD_WORKERS_DISABLED') == '1':
        # Persist test/API submissions without constructing any model thread.
        from app.services.ai_worker import submit_job
        return submit_job(kind, payload)
    with _JOB_LOCK:
        if sum(job['state'] in ('queued', 'running') for job in _JOBS.values()) >= 10:
            raise HTTPException(429, '后台语义任务队列已满，请等待当前任务完成')
        if _EXECUTOR is None:
            _EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix='local-semantic')
        uid = str(uuid4())
        job = {'id': uid, 'kind': kind, 'state': 'queued', 'created_at': time.time(), 'progress': {}, 'error': ''}
        job['entry_uuid'] = payload.get('uuid')
        _JOBS[uid] = job
        for old_id in list(_JOBS):
            if len(_JOBS) <= 30:
                break
            if _JOBS[old_id]['state'] not in ('queued', 'running'):
                _JOBS.pop(old_id)

    def run():
        global _DEFERRED_ALL
        with _JOB_LOCK:
            job['state'] = 'running'
        try:
            if kind == 'semantic_prepare':
                result = semantic.prepare_model(allow_download=True)
            elif payload.get('uuid'):
                result = semantic.index_entry(payload['uuid'], force=payload.get('force', False))
            else:
                def report(progress):
                    with _JOB_LOCK:
                        job['progress'] = progress
                result = semantic.index_all(force=payload.get('force', False), progress=report)
            with _JOB_LOCK:
                job.update(state='completed', result=result, completed_at=time.time())
        except Exception as exc:
            with _JOB_LOCK:
                job.update(state='failed', error=str(exc)[:500], completed_at=time.time())
        finally:
            # Coalesce backpressure into one later sweep instead of dropping
            # automatic updates or growing an unbounded per-record queue.
            with _JOB_LOCK:
                followup = _DEFERRED_ALL or (kind == 'semantic_prepare' and job['state'] == 'completed')
                if followup and job['state'] == 'completed':
                    _DEFERRED_ALL = False
                    if not any(item['kind'] == 'semantic_index' and item['state'] == 'queued' and not item.get('entry_uuid') for item in _JOBS.values()):
                        try:
                            _local_submit('semantic_index', {})
                        except HTTPException:
                            _DEFERRED_ALL = True

    _EXECUTOR.submit(run)
    return {'job': dict(job)}


def _submit(kind, payload):
    return _SUBMITTER(kind, payload) if _SUBMITTER else _local_submit(kind, payload)


def enqueue_index(uid):
    """Nonblocking hook for committed record writes and attachment extraction."""
    global _DEFERRED_ALL
    try:
        uid = str(UUID(uid))
    except (ValueError, TypeError, AttributeError):
        return {'state': 'ignored', 'reason': '记录 UUID 无效'}
    if semantic._MODEL is None and not semantic._manifest() and semantic._STATE not in ('loading', 'downloading'):
        return {'state': 'awaiting_model', 'uuid': uid}
    with _JOB_LOCK:
        for job in _JOBS.values():
            if job['kind'] == 'semantic_index' and job['state'] == 'queued' and (job.get('entry_uuid') == uid or not job.get('entry_uuid')):
                return {'job': dict(job), 'coalesced': True}
        try:
            return _submit('semantic_index', {'uuid': uid})
        except HTTPException as exc:
            if exc.status_code != 429:
                raise
            _DEFERRED_ALL = True
            return {'state': 'deferred', 'uuid': uid, 'reason': '已有批量任务，将在当前任务后补充索引'}


@router.get('/api/semantic/status')
def semantic_status():
    result = semantic.status()
    with _JOB_LOCK:
        result['jobs'] = [dict(job) for job in list(_JOBS.values())[-5:]]
    return result


@router.get('/api/semantic/search')
def semantic_search(request: Request, q: str = '', limit: int = 20, include_sensitive: bool = False, min_score: float = 0.35):
    if not -1 <= min_score <= 1:
        raise HTTPException(400, '相关性阈值须在 -1 到 1 之间')
    filters = {key: value for key, value in request.query_params.items()
               if key not in ('q', 'limit', 'include_sensitive', 'min_score')}
    try:
        return semantic.search(q, filters, limit=limit, include_sensitive=include_sensitive, min_score=min_score)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post('/api/semantic/prepare')
def semantic_prepare():
    return _submit('semantic_prepare', {})


@router.post('/api/semantic/index')
def semantic_index(payload: dict):
    if payload.get('scope', 'all') not in ('all', 'entry'):
        raise HTTPException(400, '索引范围须为全部或单条记录')
    if 'force' in payload and not isinstance(payload['force'], bool):
        raise HTTPException(400, '强制重新索引开关须为布尔值')
    if payload.get('scope') == 'entry' and not payload.get('uuid'):
        raise HTTPException(400, '单条索引需要记录 UUID')
    data = {'force': payload.get('force', False)}
    if payload.get('uuid'):
        try:
            data['uuid'] = str(UUID(payload['uuid']))
        except (TypeError, ValueError, AttributeError):
            raise HTTPException(400, '记录 UUID 无效')
    return _submit('semantic_index', data)


@router.get('/api/semantic/jobs/{uid}')
def semantic_job(uid: str):
    with _JOB_LOCK:
        if uid not in _JOBS:
            raise HTTPException(404, '本地语义任务不存在；统一任务请到后台任务页查看')
        return dict(_JOBS[uid])
