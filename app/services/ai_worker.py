"""Durable local jobs and draft scheduling while the application is running.

Only one worker performs model/OCR work. The scheduler is a separate lightweight
thread so a long model preparation does not prevent the day's draft being queued.
"""
from datetime import datetime
import logging
import os
from threading import Event, RLock, Thread
import time
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select

from app.database import LOCK, transaction
from app.models import now
from app.services import ai_settings, workflow
from app.services.records import serialize
from app.workflow_models import AIDraft, AIJob

KINDS = {'model_pull', 'semantic_prepare', 'semantic_index', 'attachment_extract'}
QUEUE_LIMIT = 20
DEBOUNCE_SECONDS = 10
_STATE_LOCK = RLock()
_LIFECYCLE_LOCK = RLock()
_TICK_LOCK = RLock()
_STOP = Event()
_WORKER = None
_SCHEDULER = None
_RUNTIME_STOPPER = None
_DIRTY = {}
_LAST_SCHEDULE = None
_PERIOD_CHECKED = set()
_PERIOD_GENERATION = 0
_RETRY_AT = 0.0
_DEFERRED_INDEX = False
_LOGGER = logging.getLogger('personal_database')


def _local_now():
    return datetime.now(workflow.LOCAL_TIMEZONE)


def _alive(stopped):
    return not stopped.is_set()


def _job_dict(job):
    result = serialize(job, False)
    result['state'] = result['status']
    result['entry_uuid'] = (job.payload or {}).get('uuid')
    return result


def _payload(kind, payload):
    if kind not in KINDS or not isinstance(payload, dict):
        raise HTTPException(400, '后台任务类型或参数无效')
    allowed = {'model_pull': {'model'}, 'semantic_prepare': set(),
               'semantic_index': {'uuid', 'force'}, 'attachment_extract': {'uuid', 'force'}}[kind]
    if set(payload) - allowed:
        raise HTTPException(400, '后台任务包含未知参数')
    result = dict(payload)
    if kind == 'model_pull':
        from app.services.local_ai import validate_model
        try:
            result['model'] = validate_model(result.get('model'))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    elif kind in ('semantic_index', 'attachment_extract'):
        if 'force' in result and not isinstance(result['force'], bool):
            raise HTTPException(400, '重新处理开关须为布尔值')
        result['force'] = result.get('force', False)
        if result.get('uuid'):
            try:
                result['uuid'] = str(UUID(result['uuid']))
            except (ValueError, TypeError, AttributeError) as exc:
                raise HTTPException(400, '资料 UUID 无效') from exc
        elif kind == 'attachment_extract':
            raise HTTPException(400, '附件处理需要附件 UUID')
        else:
            result.pop('uuid', None)
    return result


def submit_job(kind, payload):
    """Queue an explicitly requested job; never perform inference in a request."""
    global _DEFERRED_INDEX
    payload = _payload(kind, payload)
    with transaction() as session:
        queued = list(session.scalars(select(AIJob).where(AIJob.status == 'queued', AIJob.kind == kind)))
        # A running per-entry index must admit a later update; its source may have
        # changed after the running job took its snapshot. Preparation is immutable.
        if kind in ('model_pull', 'semantic_prepare'):
            queued += list(session.scalars(select(AIJob).where(AIJob.status == 'running', AIJob.kind == kind)))
        for job in queued:
            same = job.payload == payload
            if kind == 'semantic_index' and not job.payload.get('uuid'):
                same = not payload.get('force') or job.payload.get('force')
            if same:
                return {'job': _job_dict(job), 'coalesced': True}
        pending = session.scalar(select(func.count()).select_from(AIJob).where(AIJob.status.in_(['queued', 'running'])))
        if pending >= QUEUE_LIMIT:
            if kind == 'semantic_index':
                with _STATE_LOCK:
                    _DEFERRED_INDEX = True
            raise HTTPException(429, '后台任务队列已满，请等待当前任务完成')
        job = AIJob(kind=kind, payload=payload, status='queued', progress={}, message='等待后台处理')
        session.add(job)
        session.flush()
        return {'job': _job_dict(job), 'coalesced': False}


def record_changed():
    """Fast committed-write hook: no database, network, or thread startup here."""
    global _PERIOD_GENERATION
    date_key, stamp = _local_now().date().isoformat(), time.monotonic()
    with _STATE_LOCK:
        _DIRTY[date_key] = stamp
        _PERIOD_GENERATION += 1
        _PERIOD_CHECKED.clear()
        for old_date in sorted(_DIRTY)[:-7]:
            _DIRTY.pop(old_date, None)


def tick(*, current_time=None, monotonic_now=None, stopped=None):
    """One deterministic scheduler step, also usable by tests without threads."""
    global _LAST_SCHEDULE, _RETRY_AT
    if stopped is not None and not _alive(stopped):
        return []
    current_time = current_time or _local_now()
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=workflow.LOCAL_TIMEZONE)
    else:
        current_time = current_time.astimezone(workflow.LOCAL_TIMEZONE)
    stamp = time.monotonic() if monotonic_now is None else monotonic_now
    with _TICK_LOCK:
        if stamp < _RETRY_AT:
            return []
        try:
            config = ai_settings.get_config()
        except Exception:
            _RETRY_AT = stamp + 30
            return []
        if config.get('provider') == 'disabled' or not config.get('model'):
            return []
        if config.get('provider') == 'openai_compatible' and not config.get('cloud_consent'):
            return []
        day = current_time.date().isoformat()
        scheduled = (day, config.get('daily_time'), config.get('provider'), config.get('model'))
        due = config.get('schedule_enabled') and current_time.strftime('%H:%M') >= config.get('daily_time', '21:00')
        with _STATE_LOCK:
            dirty = dict(_DIRTY)
            period_generation = _PERIOD_GENERATION
        targets = {}
        if due and scheduled != _LAST_SCHEDULE:
            targets[day] = None
        if config.get('auto_after_save'):
            targets.update({key: value for key, value in dirty.items() if stamp - value >= DEBOUNCE_SECONDS})
        from app.services.period_review import scheduled_scopes
        periods = scheduled_scopes(current_time, config)
        period_stamp = (day, workflow.digest({key: config.get(key) for key in
            ('provider', 'model', 'base_url', 'daily_time', 'sensitivityExclude', 'include_attachments', 'cloud_consent')}))
        for period in periods:
            with _STATE_LOCK:
                if (period, period_stamp) not in _PERIOD_CHECKED:
                    targets[period] = None
        emitted = []
        for date_key, dirty_stamp in targets.items():
            if stopped is not None and not _alive(stopped):
                break
            try:
                # Holding LOCK makes stop's transition and this short enqueue
                # transaction atomic. No model or network runs in enqueue.
                with LOCK:
                    if stopped is not None and not _alive(stopped):
                        break
                    draft = workflow.enqueue(date_key if ':' in date_key else 'daily:' + date_key, config=config)
                emitted.append(draft)
            except HTTPException as exc:
                if exc.status_code == 429:
                    _RETRY_AT = stamp + 5
                    continue
                if exc.status_code != 400:
                    _RETRY_AT = stamp + 30
                    continue
                # Empty/filtered days are checked again after a committed save,
                # or next startup, instead of once every second forever.
            except Exception:
                _RETRY_AT = stamp + 30
                continue
            if due and date_key == day:
                _LAST_SCHEDULE = scheduled
            if ':' in date_key:
                with _STATE_LOCK:
                    if period_generation == _PERIOD_GENERATION:
                        _PERIOD_CHECKED.add((date_key, period_stamp))
                    if len(_PERIOD_CHECKED) > 100:
                        _PERIOD_CHECKED.intersection_update({(value, period_stamp) for value in periods})
            if dirty_stamp is not None:
                with _STATE_LOCK:
                    if _DIRTY.get(date_key) == dirty_stamp:
                        _DIRTY.pop(date_key, None)
        return emitted


def recover():
    """Called after migrations: interrupted work resumes from durable snapshots."""
    with transaction() as session:
        from app.extraction_models import AttachmentExtraction
        jobs = list(session.scalars(select(AIJob).where(AIJob.status == 'running')))
        drafts = list(session.scalars(select(AIDraft).where(AIDraft.status == 'running')))
        extractions = list(session.scalars(select(AttachmentExtraction).where(AttachmentExtraction.status == 'running')))
        for job in jobs:
            job.status, job.message, job.error = 'queued', '上次处理被中断，等待继续', ''
            job.progress = {}
        for draft in drafts:
            draft.status, draft.error = 'queued', ''
            draft.metadata_json = {key: value for key, value in (draft.metadata_json or {}).items() if key != 'progress'}
        for extraction in extractions:
            extraction.status, extraction.error, extraction.job_token = 'pending', '上次识别已中断', ''
        return {'jobs': len(jobs), 'drafts': len(drafts), 'extractions': len(extractions)}


def _progress_writer(uid, stopped):
    last_written = None

    def report(value, *, force=False):
        nonlocal last_written
        if not _alive(stopped):
            raise InterruptedError()
        stamp = time.monotonic()
        if not force and last_written is not None and stamp - last_written < 1:
            return
        # Keep only public technical progress, never provider input/response text.
        allowed = ('status', 'completed', 'total', 'digest', 'indexed_entries', 'stale_entries', 'last_uuid', 'state', 'chunks')
        data = {key: value[key] for key in allowed if key in value and isinstance(value[key], (str, int, float, bool, type(None)))}
        with transaction() as session:
            if not _alive(stopped):
                raise InterruptedError()
            job = session.scalar(select(AIJob).where(AIJob.uuid == uid))
            if job is None or job.status != 'running':
                return
            job.progress = data
            job.message = str(data.get('status', '正在后台处理'))[:200]
            job.updated_at = now()
        last_written = stamp
    return report


def _deferred_sweep(stopped):
    global _DEFERRED_INDEX
    if not _alive(stopped):
        return
    with _STATE_LOCK:
        needed = _DEFERRED_INDEX
    if not needed:
        return
    try:
        with LOCK:
            if not _alive(stopped):
                return
            submit_job('semantic_index', {})
        with _STATE_LOCK:
            _DEFERRED_INDEX = False
    except HTTPException:
        pass


def _run_job(uid, stopped):
    global _DEFERRED_INDEX
    with transaction() as session:
        if not _alive(stopped):
            return
        job = session.scalar(select(AIJob).where(AIJob.uuid == uid))
        if job is None or job.status != 'queued':
            return
        job.status, job.message, job.error = 'running', '正在后台处理', ''
        kind, payload = job.kind, dict(job.payload or {})
    report = _progress_writer(uid, stopped)
    try:
        if not _alive(stopped):
            return
        if kind == 'model_pull':
            from app.services import local_ai
            result = local_ai.pull_model(payload['model'], progress=report, config=ai_settings.get_config())
        elif kind == 'semantic_prepare':
            from app.services import semantic
            result = semantic.prepare_model(allow_download=True)
        elif kind == 'semantic_index':
            from app.services import semantic
            if payload.get('uuid'):
                result = semantic.index_entry(payload['uuid'], force=payload.get('force', False), stopped=stopped.is_set)
            else:
                result = semantic.index_all(force=payload.get('force', False), progress=report, stopped=stopped.is_set)
        elif kind == 'attachment_extract':
            from app.services.extraction import extract_attachment
            result = extract_attachment(payload['uuid'], force=payload.get('force', False), stopped=stopped.is_set)
            if result.get('status') in ('failed', 'timeout', 'unsupported'):
                raise ValueError('attachment extraction did not complete')
        else:
            raise ValueError('unknown job kind')
        report(result or {}, force=True)
        with transaction() as session:
            if not _alive(stopped):
                return
            job = session.scalar(select(AIJob).where(AIJob.uuid == uid))
            if job and job.status == 'running':
                job.status = 'completed'
                job.message = '后台处理完成'
                job.metadata_json = {'result': {key: value for key, value in (result or {}).items()
                    if key in ('state', 'model', 'model_version', 'model_ready', 'indexed_entries', 'stale_entries', 'chunks', 'warnings', 'status')}}
        if kind == 'semantic_prepare':
            with LOCK:
                if _alive(stopped):
                    try:
                        submit_job('semantic_index', {})
                    except HTTPException:
                        with _STATE_LOCK:
                            _DEFERRED_INDEX = True
        elif kind == 'semantic_index' and result.get('state') == 'stale':
            with LOCK:
                if _alive(stopped):
                    try:
                        submit_job('semantic_index', payload)
                    except HTTPException:
                        with _STATE_LOCK:
                            _DEFERRED_INDEX = True
    except Exception as exc:
        if not _alive(stopped):
            return
        # Exception messages can carry provider credentials or personal excerpts.
        message = ('中文语义模型尚未准备，请先准备模型后重试' if exc.__class__.__name__ == 'SemanticUnavailable'
                   else '后台任务未完成，请检查模型、连接或附件格式后重试')
        with transaction() as session:
            if not _alive(stopped):
                return
            job = session.scalar(select(AIJob).where(AIJob.uuid == uid))
            if job and job.status == 'running':
                job.status, job.error, job.message = 'failed', message, message
        _LOGGER.warning('Background %s failed: %s', kind, type(exc).__name__)
    _deferred_sweep(stopped)


def run_once(stopped=None, *, prefer_draft=False):
    """Perform at most one queued item. Expensive work is outside transaction()."""
    stopped = stopped or Event()
    if not _alive(stopped):
        return False
    with transaction() as session:
        if not _alive(stopped):
            return False
        job = session.scalar(select(AIJob.uuid).where(AIJob.status == 'queued').order_by(AIJob.id).limit(1))
        draft = session.scalar(select(AIDraft.uuid).where(AIDraft.status == 'queued').order_by(AIDraft.id).limit(1))
    if draft and (prefer_draft or not job):
        if not _alive(stopped):
            return False
        workflow.process_draft(draft, stopped=stopped.is_set)
        return True
    if job:
        _run_job(job, stopped)
        return True
    _deferred_sweep(stopped)
    return False


def _work_loop(stopped):
    prefer_draft = False
    while _alive(stopped):
        try:
            worked = run_once(stopped, prefer_draft=prefer_draft)
            prefer_draft = not prefer_draft if worked else prefer_draft
        except Exception as exc:
            _LOGGER.warning('Background queue unavailable: %s', type(exc).__name__)
            worked = False
        if not worked:
            stopped.wait(1)


def _schedule_loop(stopped):
    while _alive(stopped):
        tick(stopped=stopped)
        stopped.wait(1)


def start():
    """Application lifespan calls this after the database migration completed."""
    global _STOP, _WORKER, _SCHEDULER, _LAST_SCHEDULE, _RETRY_AT
    from app.semantic_routes import configure_submitter
    configure_submitter(submit_job)
    if os.getenv('PLD_WORKERS_DISABLED') == '1':
        return {'state': 'disabled'}
    with _LIFECYCLE_LOCK:
        if _WORKER is not None and _WORKER.is_alive():
            return {'state': 'stopping' if _STOP.is_set() else 'running'}
        if _RUNTIME_STOPPER is not None and _RUNTIME_STOPPER.is_alive():
            return {'state': 'stopping'}
        recover()
        _STOP = Event()
        _LAST_SCHEDULE, _RETRY_AT = None, 0.0
        _PERIOD_CHECKED.clear()
        _WORKER = Thread(target=_work_loop, args=(_STOP,), name='database-ai-worker', daemon=True)
        _SCHEDULER = Thread(target=_schedule_loop, args=(_STOP,), name='database-ai-scheduler', daemon=True)
        _WORKER.start()
        _SCHEDULER.start()
    return {'state': 'running'}


def stop():
    """Cooperatively stop in at most five seconds; leave interruptions recoverable."""
    global _RUNTIME_STOPPER
    deadline = time.monotonic() + 5
    # Synchronize with short commits before announcing the stopped transition.
    acquired = LOCK.acquire(timeout=max(0, min(.25, deadline - time.monotonic())))
    try:
        _STOP.set()
    finally:
        if acquired:
            LOCK.release()
    if os.getenv('PLD_WORKERS_DISABLED') != '1':
        from app.services.local_ai import stop_runtime
        with _STATE_LOCK:
            if _RUNTIME_STOPPER is None or not _RUNTIME_STOPPER.is_alive():
                _RUNTIME_STOPPER = Thread(target=stop_runtime, name='database-model-stop', daemon=True)
                _RUNTIME_STOPPER.start()
    for thread in (_WORKER, _SCHEDULER, _RUNTIME_STOPPER):
        if thread is not None and thread.is_alive():
            thread.join(max(0, deadline - time.monotonic()))
    return {'state': 'stopped' if not _WORKER or not _WORKER.is_alive() else 'stopping'}
