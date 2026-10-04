"""Regression checks for privacy and durable-queue boundaries discovered in review."""
from contextlib import contextmanager
from datetime import date
import json

import httpx
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.database import Base, LOCK
from app.models import DailyEntry
from app.services import ai_settings, local_ai, workflow
from app.services.records import save, serialize
from app.workflow_models import AIDraft


@pytest.fixture
def review_database(tmp_path, monkeypatch):
    engine = create_engine('sqlite:///' + (tmp_path / 'review.db').as_posix())

    @event.listens_for(engine, 'connect')
    def foreign_keys(connection, record):
        connection.execute('PRAGMA foreign_keys=ON')

    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)

    @contextmanager
    def isolated_transaction():
        with LOCK, factory.begin() as session:
            yield session

    config = dict(ai_settings.DEFAULTS, provider='openai_compatible', model='fixture-model',
                  base_url='https://fixture.invalid/v1', cloud_consent=True, include_attachments=False)
    monkeypatch.setattr(workflow, 'transaction', isolated_transaction)
    monkeypatch.setattr(ai_settings, 'get_config', lambda: dict(config))
    yield factory, config
    engine.dispose()


def fixture_provider(items, config, schema, prompt):
    return dict(title='整理草稿', summary='核对原文后整理。', category='每日总结', tags=[],
                segments=[dict(section='处理过程', text=item['text'],
                               evidence=[dict(source_uuid=item['source_uuid'], quote=item['text'][:100])])
                          for item in items], questions=[], warnings=[])


def create_source(factory):
    with factory.begin() as session:
        return save(session, 'entries', dict(date=date(2035, 6, 7), title='原始记录',
            content='今天对设备参数进行了检查，记录了处理过程和后续计划，等待明日复核。')).uuid


def read_draft(factory, uid):
    with factory() as session:
        return serialize(session.scalar(select(AIDraft).where(AIDraft.uuid == uid)), False)


@pytest.mark.parametrize('archive_change', ['sensitive', 'deleted', 'edited'])
def test_changed_prior_archive_is_not_sent_to_cloud(review_database, archive_change):
    factory, config = review_database
    source_uid = create_source(factory)
    first = workflow.enqueue('entry:' + source_uid, config)
    workflow.process_draft(first['uuid'], provider=fixture_provider)
    first = read_draft(factory, first['uuid'])
    approved = workflow.approve(first['uuid'], first['revision'])
    with factory.begin() as session:
        source = session.scalar(select(DailyEntry).where(DailyEntry.uuid == source_uid))
        source.content += '新增了独立的复核说明。'
    queued = workflow.enqueue('entry:' + source_uid, config)
    assert any(row.get('prior_approved_archive') for row in queued['source_records'])
    with factory.begin() as session:
        archive = session.scalar(select(DailyEntry).where(DailyEntry.uuid == approved['archived_uuid']))
        if archive_change == 'sensitive':
            archive.privacy_level = 3
        elif archive_change == 'deleted':
            archive.deleted_at = '2035-06-07T22:00:00'
        else:
            archive.content += '这是刚刚人工编辑的说明，旧稿不能再次上传。'
    calls = []

    def forbidden_provider(*args):
        calls.append(True)
        return fixture_provider(*args)

    workflow.process_draft(queued['uuid'], provider=forbidden_provider)
    assert not calls, 'Stale prior-approved archive text must not be sent to the provider.'
    assert read_draft(factory, queued['uuid'])['status'] == 'failed'


def test_oversized_changed_source_fails_draft_without_poisoning_queue(review_database):
    factory, config = review_database
    source_uid = create_source(factory)
    queued = workflow.enqueue('entry:' + source_uid, config)
    with factory.begin() as session:
        source = session.scalar(select(DailyEntry).where(DailyEntry.uuid == source_uid))
        source.content = '事实' * 120000
    calls = []

    def forbidden_provider(*args):
        calls.append(True)
        return fixture_provider(*args)

    workflow.process_draft(queued['uuid'], provider=forbidden_provider)
    assert not calls
    assert read_draft(factory, queued['uuid'])['status'] == 'failed'


def test_revoked_cloud_consent_stops_later_chunks(review_database):
    factory, config = review_database
    with factory.begin() as session:
        source_uid = save(session, 'entries', dict(date=date(2035, 6, 7), title='多段原文',
            content='今天检查参数，记录核实过程与结果。' * 400)).uuid
    queued = workflow.enqueue('entry:' + source_uid, config)
    calls = []

    def provider(items, *args):
        calls.append(items)
        if len(calls) == 1:
            config['cloud_consent'] = False
        return fixture_provider(items, *args)

    workflow.process_draft(queued['uuid'], provider=provider)
    assert len(calls) == 1, 'Revoked consent must prevent subsequent network uploads.'
    assert read_draft(factory, queued['uuid'])['status'] == 'failed'


REMOTE_MARKERS = [
    {'remote_host': 'https://upstream.invalid'},
    {'remote_model': 'upstream-model'},
    {'remote_host': 'https://upstream.invalid', 'remote_model': 'upstream-model'},
]


@pytest.mark.parametrize('remote_markers', REMOTE_MARKERS)
def test_local_model_list_excludes_remote_aliases(monkeypatch, remote_markers):
    alias = dict(name='work-notes:latest', model='work-notes:latest', **remote_markers)

    def respond(request):
        assert request.url.path == '/api/tags'
        return httpx.Response(200, json={'models': [alias, {'name': 'local-fixture:latest'}]})

    monkeypatch.setattr(local_ai, 'ensure_runtime', lambda config: True)
    monkeypatch.setattr(local_ai, '_client', lambda *args: httpx.Client(transport=httpx.MockTransport(respond)))
    result = local_ai.models({'provider': 'ollama', 'base_url': 'http://127.0.0.1:11435'})
    assert result['models'] == [{'name': 'local-fixture:latest'}]


@pytest.mark.parametrize('remote_markers', REMOTE_MARKERS)
def test_local_organization_blocks_remote_alias_before_any_source_upload(monkeypatch, remote_markers):
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.path == '/api/show':
            assert json.loads(request.content) == {'model': 'work-notes:latest'}
            return httpx.Response(200, json=remote_markers | {'capabilities': ['completion'],
                'model_info': {'general.architecture': 'fixture'}})
        if request.url.path == '/api/tags':
            return httpx.Response(200, json={'models': [dict(name='work-notes:latest', **remote_markers)]})
        return httpx.Response(200, json={'message': {'content': '{"title":"fixture"}'}})

    monkeypatch.setattr(local_ai, 'ensure_runtime', lambda config: True)
    monkeypatch.setattr(local_ai, '_client', lambda *args: httpx.Client(transport=httpx.MockTransport(respond)))
    with pytest.raises(ValueError):
        local_ai.organize([{'text': 'SYNTHETIC-LOCAL-ONLY-RECORD'}],
            dict(provider='ollama', base_url='http://127.0.0.1:11435', model='work-notes:latest', cloud_consent=False),
            {'type': 'object'})
    assert requests and not any(request.url.path == '/api/chat' for request in requests)
    assert not any(b'SYNTHETIC-LOCAL-ONLY-RECORD' in request.content for request in requests)


LOCAL_MODEL_INFO = {'model_info': {'general.architecture': 'qwen2'},
                    'details': {'format': 'gguf', 'family': 'qwen2', 'parameter_size': '1.5B'}}


@pytest.mark.parametrize('model_name', ['qwen2.5:1.5b', 'my-local-note:latest'])
def test_verified_local_model_and_local_alias_still_generate(monkeypatch, model_name):
    requests = []
    source = {'text': 'SYNTHETIC-LOCAL-ONLY-RECORD'}

    def respond(request):
        requests.append(request)
        assert request.url.host == '127.0.0.1'
        assert request.method == 'POST'
        if request.url.path == '/api/show':
            assert json.loads(request.content) == {'model': model_name}
            assert b'SYNTHETIC-LOCAL-ONLY-RECORD' not in request.content
            return httpx.Response(200, json=LOCAL_MODEL_INFO)
        assert request.url.path == '/api/chat'
        payload = json.loads(request.content)
        assert payload['model'] == model_name
        assert json.loads(payload['messages'][1]['content']) == {'source_records': [source]}
        return httpx.Response(200, json={'message': {'content': '{"title":"fixture draft"}'}})

    monkeypatch.setattr(local_ai, 'ensure_runtime', lambda config: True)
    monkeypatch.setattr(local_ai, '_client', lambda *args: httpx.Client(transport=httpx.MockTransport(respond)))
    config = dict(provider='ollama', base_url='http://127.0.0.1:11435', model=model_name, cloud_consent=False)
    # A previous successful check must not replace checking the next request.
    for _ in range(2):
        assert local_ai.organize([source], config, {'type': 'object'}) == {'title': 'fixture draft'}
    assert [request.url.path for request in requests] == ['/api/show', '/api/chat', '/api/show', '/api/chat']


@pytest.mark.parametrize('failure', ['unavailable', 'missing_model', 'service_error', 'invalid_json', 'disconnected'])
def test_failed_local_inspection_never_uploads_sources(monkeypatch, failure):
    requests = []

    def respond(request):
        requests.append(request)
        assert request.url.path == '/api/show'
        assert json.loads(request.content) == {'model': 'fixture-local:latest'}
        if failure == 'unavailable':
            return httpx.Response(503, json={'error': 'fixture unavailable'})
        if failure == 'missing_model':
            return httpx.Response(404, json={'error': 'fixture missing'})
        if failure == 'service_error':
            return httpx.Response(200, json=LOCAL_MODEL_INFO | {'error': 'fixture error'})
        if failure == 'invalid_json':
            return httpx.Response(200, text='not a JSON response')
        raise httpx.ConnectError('fixture disconnected', request=request)

    monkeypatch.setattr(local_ai, 'ensure_runtime', lambda config: True)
    monkeypatch.setattr(local_ai, '_client', lambda *args: httpx.Client(transport=httpx.MockTransport(respond)))
    with pytest.raises((ValueError, httpx.HTTPError)):
        local_ai.organize([{'text': 'SYNTHETIC-LOCAL-ONLY-RECORD'}],
            dict(provider='ollama', base_url='http://127.0.0.1:11435', model='fixture-local:latest'), {'type': 'object'})
    assert len(requests) == 1
    assert not any(b'SYNTHETIC-LOCAL-ONLY-RECORD' in request.content for request in requests)


@pytest.mark.parametrize('inspection', [
    {},
    {'model_info': {'general.architecture': 'fixture'}},
    {'details': {'format': 'gguf'}},
    {'model_info': {}, 'details': {'format': 'gguf'}},
    {'model_info': {'general.architecture': 'fixture'}, 'details': {}},
])
def test_missing_local_metadata_never_uploads_sources(monkeypatch, inspection):
    requests = []

    def respond(request):
        requests.append(request)
        assert request.url.path == '/api/show'
        return httpx.Response(200, json=inspection)

    monkeypatch.setattr(local_ai, 'ensure_runtime', lambda config: True)
    monkeypatch.setattr(local_ai, '_client', lambda *args: httpx.Client(transport=httpx.MockTransport(respond)))
    with pytest.raises(ValueError):
        local_ai.organize([{'text': 'SYNTHETIC-LOCAL-ONLY-RECORD'}],
            dict(provider='ollama', base_url='http://127.0.0.1:11435', model='fixture-local:latest'), {'type': 'object'})
    assert len(requests) == 1
    assert not any(b'SYNTHETIC-LOCAL-ONLY-RECORD' in request.content for request in requests)
