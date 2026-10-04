"""Scheduler and durable worker regression tests never launch real models."""
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from threading import Event
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from app.database import Base, LOCK
from app.extraction_models import AttachmentExtraction
from app.models import Attachment, DailyEntry
from app.services import ai_settings, ai_worker, local_ai, semantic, workflow
from app.workflow_models import AIDraft, AIJob

DAY = date(2034, 5, 6)


@pytest.fixture
def database(tmp_path, monkeypatch):
    engine = create_engine('sqlite:///' + (tmp_path / 'worker.db').as_posix())
    @event.listens_for(engine, 'connect')
    def foreign_keys(connection, record):
        connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    @contextmanager
    def transaction():
        with LOCK, factory.begin() as session:
            yield session
    for module in (ai_worker, workflow, ai_settings):
        monkeypatch.setattr(module, 'transaction', transaction)
    config = dict(ai_settings.DEFAULTS, schedule_enabled=True, auto_after_save=True,
                  daily_time='21:00', include_attachments=False, api_key='')
    monkeypatch.setattr(ai_settings, 'get_config', lambda: dict(config))
    monkeypatch.setattr(ai_worker, '_DIRTY', {})
    monkeypatch.setattr(ai_worker, '_LAST_SCHEDULE', None)
    monkeypatch.setattr(ai_worker, '_PERIOD_CHECKED', set())
    monkeypatch.setattr(ai_worker, '_PERIOD_GENERATION', 0)
    monkeypatch.setattr(ai_worker, '_RETRY_AT', 0.0)
    monkeypatch.setattr(ai_worker, '_DEFERRED_INDEX', False)
    monkeypatch.setattr(ai_worker, '_STOP', Event())
    monkeypatch.setattr(ai_worker, '_WORKER', None)
    monkeypatch.setattr(ai_worker, '_SCHEDULER', None)
    monkeypatch.setattr(ai_worker, '_RUNTIME_STOPPER', None)
    yield factory, config
    engine.dispose()


def entry(factory, **values):
    with factory.begin() as session:
        row = DailyEntry(date=DAY, title='现场处理', content='更换轴承后振动消失',
                         original_content='更换轴承后振动消失', **values)
        session.add(row)
        session.flush()
        return row.uuid


def at(hour, minute=0, day=DAY):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=workflow.LOCAL_TIMEZONE)


def test_daily_scheduler_catches_up_after_restart_without_duplicate(database):
    factory, config = database
    entry(factory)
    assert ai_worker.tick(current_time=at(20, 59), monotonic_now=1) == []
    first = ai_worker.tick(current_time=at(21, 0), monotonic_now=2)
    assert len(first) == 1
    assert ai_worker.tick(current_time=at(22), monotonic_now=3) == []
    ai_worker._LAST_SCHEDULE = None  # An application reopened later that day.
    second = ai_worker.tick(current_time=at(23), monotonic_now=4)
    assert second[0]['uuid'] == first[0]['uuid']
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(AIDraft)) == 1


def test_save_debounce_resets_timer_and_deduplicates_unchanged_sources(database, monkeypatch):
    factory, config = database
    entry(factory)
    config['schedule_enabled'] = False
    clock = [100.0]
    monkeypatch.setattr(ai_worker.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(ai_worker, '_local_now', lambda: at(12))
    ai_worker.record_changed()
    clock[0] = 109
    assert ai_worker.tick(current_time=at(12), monotonic_now=109) == []
    ai_worker.record_changed()
    assert ai_worker.tick(current_time=at(12), monotonic_now=118) == []
    first = ai_worker.tick(current_time=at(12), monotonic_now=119)
    assert len(first) == 1 and ai_worker._DIRTY == {}
    clock[0] = 120
    ai_worker.record_changed()
    second = ai_worker.tick(current_time=at(12), monotonic_now=130)
    assert second[0]['uuid'] == first[0]['uuid']


def test_scheduler_excludes_sensitive_deleted_and_generated_archive(database):
    factory, config = database
    public = entry(factory)
    entry(factory, privacy_level=3)
    entry(factory, deleted_at='2034-05-06T12:00:00')
    entry(factory, metadata_json={'ai_workflow_archive': True})
    queued = ai_worker.tick(current_time=at(22), monotonic_now=100)[0]
    assert [row['uuid'] for row in queued['source_records']] == [public]
    assert queued['status'] == 'queued'
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(DailyEntry)) == 4


def test_disabled_provider_and_unconsented_cloud_never_enqueue(database):
    factory, config = database
    entry(factory)
    config['provider'] = 'disabled'
    assert ai_worker.tick(current_time=at(22), monotonic_now=100) == []
    config['provider'] = 'openai_compatible'
    config['cloud_consent'] = False
    assert ai_worker.tick(current_time=at(22), monotonic_now=101) == []
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(AIDraft)) == 0


def test_empty_day_quiet_until_a_new_save_and_dirty_history_bounded(database, monkeypatch):
    factory, config = database
    assert ai_worker.tick(current_time=at(22), monotonic_now=100) == []
    assert ai_worker._LAST_SCHEDULE is not None
    entry(factory)
    monkeypatch.setattr(ai_worker, '_local_now', lambda: at(22))
    monkeypatch.setattr(ai_worker.time, 'monotonic', lambda: 110)
    ai_worker.record_changed()
    assert len(ai_worker.tick(current_time=at(22), monotonic_now=120)) == 1
    for offset in range(20):
        monkeypatch.setattr(ai_worker, '_local_now', lambda offset=offset: at(12, day=DAY + timedelta(days=offset)))
        ai_worker.record_changed()
    assert len(ai_worker._DIRTY) == 7


def test_recover_only_running_jobs_drafts_and_extractions(database):
    factory, config = database
    uid = entry(factory)
    draft = workflow.enqueue('daily:' + DAY.isoformat(), config=config)
    job = ai_worker.submit_job('semantic_prepare', {})['job']
    with factory.begin() as session:
        session.scalar(select(AIDraft).where(AIDraft.uuid == draft['uuid'])).status = 'running'
        session.scalar(select(AIJob).where(AIJob.uuid == job['uuid'])).status = 'running'
        session.add(AIJob(kind='model_pull', status='completed', payload={'model': 'qwen2.5:1.5b'}))
        row = session.scalar(select(DailyEntry).where(DailyEntry.uuid == uid))
        attachment = Attachment(original_filename='a.txt', stored_filename='a.txt', file_path='a.txt',
                                file_size=1, sha256='a' * 64, entry_id=row.id)
        session.add(attachment)
        session.flush()
        session.add(AttachmentExtraction(attachment_uuid=attachment.uuid, source_sha256=attachment.sha256,
                                        status='running', job_token=str(uuid4())))
    assert ai_worker.recover() == {'jobs': 1, 'drafts': 1, 'extractions': 1}
    assert ai_worker.recover() == {'jobs': 0, 'drafts': 0, 'extractions': 0}
    with factory() as session:
        assert session.scalar(select(AIDraft)).status == 'queued'
        assert session.scalar(select(AttachmentExtraction)).job_token == ''
        assert session.scalar(select(AIJob).where(AIJob.status == 'completed')) is not None


def test_durable_queue_coalesces_and_preserves_an_edit_during_running_index(database, monkeypatch):
    factory, config = database
    uid = str(uuid4())
    first = ai_worker.submit_job('semantic_index', {'uuid': uid})
    assert ai_worker.submit_job('semantic_index', {'uuid': uid})['coalesced']
    assert first['job']['state'] == first['job']['status'] == 'queued'
    with factory.begin() as session:
        session.scalar(select(AIJob)).status = 'running'
    second = ai_worker.submit_job('semantic_index', {'uuid': uid})
    assert second['job']['uuid'] != first['job']['uuid']
    monkeypatch.setattr(ai_worker, 'QUEUE_LIMIT', 2)
    with pytest.raises(HTTPException) as exc:
        ai_worker.submit_job('semantic_index', {'uuid': str(uuid4())})
    assert exc.value.status_code == 429 and ai_worker._DEFERRED_INDEX
    with factory.begin() as session:
        for job in session.scalars(select(AIJob)):
            job.status = 'completed'
    ai_worker._deferred_sweep(Event())
    with factory() as session:
        sweep = session.scalar(select(AIJob).where(AIJob.status == 'queued'))
        assert 'uuid' not in sweep.payload


def test_model_progress_is_throttled_persistent_and_outside_database_lock(database, monkeypatch):
    factory, config = database
    clock = [0.0]
    monkeypatch.setattr(ai_worker.time, 'monotonic', lambda: clock[0])
    uid = ai_worker.submit_job('model_pull', {'model': 'qwen2.5:1.5b'})['job']['uuid']
    observed = []
    def pull(model, progress, config):
        assert not LOCK._is_owned()
        for stamp, completed in ((0, 1), (.2, 2), (1.1, 3)):
            clock[0] = stamp
            progress({'status': 'pulling', 'completed': completed, 'total': 3, 'private': 'do not persist'})
            with factory() as session:
                observed.append(session.scalar(select(AIJob)).progress['completed'])
        return {'status': 'success'}
    monkeypatch.setattr(local_ai, 'pull_model', pull)
    assert ai_worker.run_once()
    assert observed == [1, 1, 3]
    with factory() as session:
        row = session.scalar(select(AIJob))
        assert row.status == 'completed' and 'private' not in row.progress


def test_stop_during_io_leaves_recoverable_running_without_final_write(database, monkeypatch):
    factory, config = database
    stopped = Event()
    ai_worker.submit_job('model_pull', {'model': 'qwen2.5:1.5b'})
    def pull(*args, **kwargs):
        stopped.set()
        return {'status': 'success'}
    monkeypatch.setattr(local_ai, 'pull_model', pull)
    assert ai_worker.run_once(stopped)
    with factory() as session:
        assert session.scalar(select(AIJob)).status == 'running'
    assert not ai_worker.run_once(stopped)
    assert ai_worker.recover()['jobs'] == 1


def test_failure_does_not_store_provider_secret_or_record_text(database, monkeypatch):
    factory, config = database
    ai_worker.submit_job('model_pull', {'model': 'qwen2.5:1.5b'})
    def pull(*args, **kwargs):
        raise RuntimeError('Authorization Bearer secret and private record text')
    monkeypatch.setattr(local_ai, 'pull_model', pull)
    ai_worker.run_once()
    with factory() as session:
        row = session.scalar(select(AIJob))
        assert row.status == 'failed'
        assert 'secret' not in row.error and 'private record' not in row.error


def test_disabled_workers_start_has_no_model_threads_and_api_queue_still_persists(database, monkeypatch):
    factory, config = database
    from app import semantic_routes
    monkeypatch.setenv('PLD_WORKERS_DISABLED', '1')
    monkeypatch.setattr(semantic_routes, '_SUBMITTER', None)
    def forbidden(*args, **kwargs):
        raise AssertionError('tests must not create background threads')
    monkeypatch.setattr(ai_worker, 'Thread', forbidden)
    assert ai_worker.start()['state'] == 'disabled'
    result = semantic_routes._submit('semantic_prepare', {})
    assert result['job']['status'] == 'queued'
    assert ai_worker._WORKER is None


def test_queue_payload_does_not_accept_secrets_or_invalid_force(database):
    with pytest.raises(HTTPException):
        ai_worker.submit_job('model_pull', {'model': 'qwen2.5:1.5b', 'api_key': 'secret'})
    with pytest.raises(HTTPException):
        ai_worker.submit_job('semantic_index', {'force': 'false'})


def test_semantic_prepare_queues_one_full_index_and_draft_handoff_is_cancellable(database, monkeypatch):
    factory, config = database
    ai_worker.submit_job('semantic_prepare', {})
    monkeypatch.setattr(semantic, 'prepare_model', lambda **kwargs: {'state': 'ready', 'model_ready': True})
    ai_worker.run_once()
    with factory() as session:
        assert session.scalar(select(AIJob).where(AIJob.status == 'queued')).kind == 'semantic_index'
    entry(factory)
    draft = workflow.enqueue('daily:' + DAY.isoformat(), config=config)
    calls = []
    def process(uid, stopped):
        assert not LOCK._is_owned() and not stopped()
        calls.append(uid)
    monkeypatch.setattr(workflow, 'process_draft', process)
    ai_worker.run_once(prefer_draft=True)
    assert calls == [draft['uuid']]


def test_period_empty_check_is_invalidated_by_same_day_backfill(database, monkeypatch):
    factory, config = database
    config.update(schedule_enabled=False, auto_after_save=False, weekly_enabled=True)
    moment = at(22)
    assert ai_worker.tick(current_time=moment, monotonic_now=1) == []
    from app.services.period_review import scheduled_scopes, date_range
    period = scheduled_scopes(moment, config)[0]
    start, _ = date_range(period)
    with factory.begin() as session:
        session.add(DailyEntry(date=start, title='补录上周', content='补录上周实际处理事项', original_content='补录上周实际处理事项'))
    monkeypatch.setattr(ai_worker, '_local_now', lambda: moment)
    ai_worker.record_changed()
    emitted = ai_worker.tick(current_time=moment, monotonic_now=2)
    assert len(emitted) == 1 and emitted[0]['scope_key'] == period
    assert ai_worker.tick(current_time=moment, monotonic_now=3) == []
