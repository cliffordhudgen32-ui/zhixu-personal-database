"""Human approval is mandatory; provider fixtures never contact a model service."""
from contextlib import contextmanager
from datetime import date
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from app.database import Base, LOCK
from app.extraction_models import AttachmentExtraction
from app.knowledge_models import KnowledgeRelation, Revision
from app.models import Attachment, DailyEntry, now
from app.services import workflow
from app.services.records import save, serialize
from app.workflow_models import AIDraft, AIArchive, AIReviewRevision

TEST_DATE = date(2030, 1, 2)
CONFIG = dict(provider='ollama', model='test-model', base_url='http://127.0.0.1:11435',
              sensitivityExclude=True, include_attachments=True, cloud_consent=False,
              auto_after_save=False, schedule_enabled=False)


@pytest.fixture
def database(tmp_path, monkeypatch):
    engine = create_engine('sqlite:///' + (tmp_path / 'workflow.db').as_posix())

    @event.listens_for(engine, 'connect')
    def configure(connection, record):
        connection.execute('PRAGMA foreign_keys=ON')

    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)

    @contextmanager
    def isolated_transaction():
        with LOCK, factory.begin() as session:
            yield session

    from app.services import ai_settings
    from app import ai_routes
    monkeypatch.setattr(workflow, 'transaction', isolated_transaction)
    monkeypatch.setattr(ai_routes, 'transaction', isolated_transaction)
    monkeypatch.setattr(ai_settings, 'get_config', lambda: dict(CONFIG))
    yield factory
    engine.dispose()


def make_entry(database, content='原始工作记录：今天检查参数，发现问题并记录后续计划。', **values):
    with database.begin() as session:
        return save(session, 'entries', dict(date=TEST_DATE, title='当天原始记录', content=content, **values)).uuid


def read(database, model, uid):
    with database() as session:
        return serialize(session.scalar(select(model).where(model.uuid == uid)), False)


def fake_provider(items, config, schema, prompt):
    assert not LOCK._is_owned(), 'Model inference must run outside the global database lock'
    assert 'segments' in schema['properties'] and 'source_uuid' in str(schema)
    assert '不要虚构' in prompt
    segments = []
    for source in items:
        evidence = {key: source[key] for key in ('source_uuid', 'attachment_uuid', 'page') if key in source}
        evidence['quote'] = source['text'][:100]
        segments.append(dict(section='处理过程', text=source['text'][:500], evidence=[evidence]))
    return dict(title='当天整理草稿', summary='依据记录整理，等待人工确认。', category='每日总结', tags=['工作整理'],
                segments=segments, questions=[], warnings=[])


def enqueue_and_generate(database, source_uid, provider=fake_provider, config=None):
    queued = workflow.enqueue('entry:' + source_uid, config=config or CONFIG)
    workflow.process_draft(queued['uuid'], provider=provider)
    return read(database, AIDraft, queued['uuid'])


def edit(database, draft, **changes):
    from app import ai_routes
    payload = {key: draft[key] for key in ('revision', 'title', 'content', 'summary', 'category', 'tags', 'answers')}
    payload.update(changes)
    return ai_routes.edit_draft(draft['uuid'], ai_routes.DraftEdit.model_validate(payload))


def test_generation_creates_only_draft_and_never_rewrites_source(database):
    uid = make_entry(database)
    original = read(database, DailyEntry, uid)
    queued = workflow.enqueue('entry:' + uid, CONFIG)
    assert queued['status'] == 'queued'
    workflow.process_draft(queued['uuid'], provider=fake_provider)
    draft = read(database, AIDraft, queued['uuid'])
    assert draft['status'] == 'pending_review' and draft['evidence']
    assert read(database, DailyEntry, uid) == original
    with database() as session:
        assert session.scalar(select(func.count(DailyEntry.id))) == 1
        assert session.scalar(select(func.count(AIArchive.scope_key))) == 0
        revisions = list(session.scalars(select(AIReviewRevision)))
        assert len(revisions) == 1 and revisions[0].action == 'generated'


@pytest.mark.parametrize('bad_citation', ['quote', 'source', 'attachment', 'page', 'empty_evidence'])
def test_invalid_evidence_never_enters_review(database, bad_citation):
    uid = make_entry(database)

    def bad_provider(*args):
        value = fake_provider(*args)
        citation = value['segments'][0]['evidence'][0]
        if bad_citation == 'quote':
            citation['quote'] = '这句话完全不存在于原始记录'
        elif bad_citation == 'source':
            citation['source_uuid'] = str(uuid4())
        elif bad_citation == 'attachment':
            citation['attachment_uuid'] = str(uuid4())
        elif bad_citation == 'page':
            citation['page'] = 7
        else:
            value['segments'][0]['evidence'] = []
        return value

    draft = enqueue_and_generate(database, uid, bad_provider)
    assert draft['status'] == 'failed' and not draft['archived_uuid']
    with database() as session:
        assert session.scalar(select(func.count(DailyEntry.id))) == 1


def test_provider_errors_do_not_leak_private_content_or_secret(database):
    uid = make_entry(database)

    def failing_provider(*args):
        raise RuntimeError('Authorization: Bearer SUPER-SECRET; private original body')

    draft = enqueue_and_generate(database, uid, failing_provider)
    assert draft['status'] == 'failed'
    assert 'SUPER-SECRET' not in draft['error'] and 'private original body' not in draft['error']


def test_edit_revision_conflict_and_history_preserve_manual_work(database):
    uid = make_entry(database)
    draft = enqueue_and_generate(database, uid)
    updated = edit(database, draft, title='人工修改标题', content='人工补充：实际结果尚未确认。')
    assert updated['revision'] == draft['revision'] + 1
    with pytest.raises(HTTPException) as error:
        edit(database, draft, content='另一个过期窗口的改动')
    assert error.value.status_code == 409
    current = read(database, AIDraft, draft['uuid'])
    assert current['content'] == '人工补充：实际结果尚未确认。'
    with database() as session:
        edits = list(session.scalars(select(AIReviewRevision).where(AIReviewRevision.action == 'edited')))
        assert edits[0].snapshot['content'] == current['content']
    assert read(database, DailyEntry, uid)['original_content'].startswith('原始工作记录')


@pytest.mark.parametrize('change', ['content', 'delete', 'privacy'])
def test_source_change_blocks_approval_without_losing_review(database, change):
    uid = make_entry(database)
    draft = enqueue_and_generate(database, uid)
    draft = edit(database, draft, content='人工仔细审核后的文本')
    with database.begin() as session:
        source = session.scalar(select(DailyEntry).where(DailyEntry.uuid == uid))
        if change == 'content':
            source.content = '在审核期间更新的原文'
        elif change == 'delete':
            source.deleted_at = now()
        else:
            source.privacy_level = 3
    with pytest.raises(HTTPException) as error:
        workflow.approve(draft['uuid'], draft['revision'])
    assert error.value.status_code == 409
    assert read(database, AIDraft, draft['uuid'])['content'] == '人工仔细审核后的文本'
    with database() as session:
        assert session.scalar(select(func.count(AIArchive.scope_key))) == 0


def test_privacy_and_recycle_bin_are_filtered_before_provider(database):
    public = make_entry(database, content='可整理的公开内容')
    secret = make_entry(database, content='PRIVATE-MARKER', privacy_level=3)
    deleted = make_entry(database, content='DELETED-MARKER')
    with database.begin() as session:
        session.scalar(select(DailyEntry).where(DailyEntry.uuid == deleted)).deleted_at = now()
    calls = []

    def capturing_provider(items, *args):
        calls.extend(items)
        return fake_provider(items, *args)

    queued = workflow.enqueue('daily:' + TEST_DATE.isoformat(), CONFIG)
    assert [record['uuid'] for record in queued['source_records']] == [public]
    workflow.process_draft(queued['uuid'], provider=capturing_provider)
    assert calls and {item['source_uuid'] for item in calls} == {public}
    assert 'PRIVATE-MARKER' not in str(calls) and 'DELETED-MARKER' not in str(calls)
    with pytest.raises(HTTPException) as error:
        workflow.enqueue('entry:' + secret, CONFIG)
    assert error.value.status_code == 400


def test_approval_is_idempotent_and_new_source_updates_same_archive(database):
    uid = make_entry(database)
    source_original = read(database, DailyEntry, uid)['original_content']
    first = enqueue_and_generate(database, uid)
    approved = workflow.approve(first['uuid'], first['revision'])
    again = workflow.approve(first['uuid'], first['revision'])
    assert again['archived_uuid'] == approved['archived_uuid']
    archive_uid = approved['archived_uuid']
    initial_archive = read(database, DailyEntry, archive_uid)
    assert initial_archive['generated_by_ai'] and initial_archive['is_archived']
    assert source_original in initial_archive['original_content']
    assert workflow.enqueue('entry:' + uid, CONFIG)['uuid'] == first['uuid']
    with database.begin() as session:
        source = session.scalar(select(DailyEntry).where(DailyEntry.uuid == uid))
        values = {key: value for key, value in serialize(source).items() if key in __import__('app.schemas', fromlist=['EntryInput']).EntryInput.model_fields}
        values.update(content='后来验证了问题原因，增加新的结果。', tags=[], projects=[], people=[])
        save(session, 'entries', values, uid)
    second = enqueue_and_generate(database, uid)
    assert second['uuid'] != first['uuid']
    approved_second = workflow.approve(second['uuid'], second['revision'], acknowledge_uncertain=True)
    assert approved_second['archived_uuid'] == archive_uid
    assert read(database, DailyEntry, archive_uid)['original_content'] == initial_archive['original_content']
    assert read(database, DailyEntry, uid)['original_content'] == source_original
    with database() as session:
        assert session.scalar(select(func.count(DailyEntry.id))) == 2
        assert session.scalar(select(func.count(AIArchive.scope_key))) == 1
        assert session.scalar(select(func.count(Revision.id)).where(Revision.item_uuid == archive_uid)) == 1
        assert session.scalar(select(func.count(KnowledgeRelation.id)).where(KnowledgeRelation.from_uuid == archive_uid, KnowledgeRelation.to_uuid == uid)) == 1


def test_manual_archive_edit_blocks_a_queued_replacement(database):
    uid = make_entry(database)
    first = enqueue_and_generate(database, uid)
    approved = workflow.approve(first['uuid'], first['revision'])
    with database.begin() as session:
        session.scalar(select(DailyEntry).where(DailyEntry.uuid == uid)).content = '新的来源内容'
    replacement = enqueue_and_generate(database, uid)
    with database.begin() as session:
        session.scalar(select(DailyEntry).where(DailyEntry.uuid == approved['archived_uuid'])).content = '归档中的人工修改'
    with pytest.raises(HTTPException) as error:
        workflow.approve(replacement['uuid'], replacement['revision'])
    assert error.value.status_code == 409
    assert read(database, DailyEntry, approved['archived_uuid'])['content'] == '归档中的人工修改'


def questioning_provider(items, *args):
    document = fake_provider(items, *args)
    document['questions'] = [dict(id='', question='实际处理结果是什么？', reason='原文尚未说明结果', source_uuids=[items[0]['source_uuid']])]
    document['warnings'] = ['仍有未核实信息，确认前请检查。']
    return document


def test_answers_persist_and_approval_requires_explicit_acknowledgement(database):
    uid = make_entry(database)
    draft = enqueue_and_generate(database, uid, questioning_provider)
    question_id = draft['questions'][0]['id']
    revised = edit(database, draft, answers={question_id: '已完成复查，结果符合预期。'})
    assert read(database, AIDraft, draft['uuid'])['answers'][question_id] == '已完成复查，结果符合预期。'
    with pytest.raises(HTTPException) as error:
        workflow.approve(revised['uuid'], revised['revision'])
    assert error.value.status_code == 400
    approved = workflow.approve(revised['uuid'], revised['revision'], acknowledge_uncertain=True)
    entry = read(database, DailyEntry, approved['archived_uuid'])
    assert '已完成复查，结果符合预期。' in entry['content']
    assert entry['metadata_json']['uncertainty_acknowledged'] is True
    with database() as session:
        history = list(session.scalars(select(AIReviewRevision).where(AIReviewRevision.draft_uuid == draft['uuid'])))
        assert any(row.snapshot.get('answers', {}).get(question_id) == '已完成复查，结果符合预期。' for row in history)


def test_unknown_question_answers_are_rejected(database):
    uid = make_entry(database)
    draft = enqueue_and_generate(database, uid, questioning_provider)
    with pytest.raises(HTTPException) as error:
        edit(database, draft, answers={'invented-question': '错误关联答案'})
    assert error.value.status_code == 400


def test_retry_new_questions_cannot_inherit_old_question_answers(database):
    from app import ai_routes
    uid = make_entry(database)
    draft = enqueue_and_generate(database, uid, questioning_provider)
    draft = edit(database, draft, answers={'q1': '这个答案只对应旧问题'})
    ai_routes.reject_draft(draft['uuid'])
    ai_routes.retry_draft(draft['uuid'])

    def different_question(items, *args):
        result = questioning_provider(items, *args)
        result['questions'][0]['question'] = '由谁负责下次复查？'
        return result

    workflow.process_draft(draft['uuid'], provider=different_question)
    current = read(database, AIDraft, draft['uuid'])
    assert current['questions'][0]['question'] == '由谁负责下次复查？'
    assert current['answers'] == {}
    with database() as session:
        history = list(session.scalars(select(AIReviewRevision).where(AIReviewRevision.draft_uuid == draft['uuid'])))
        old = [row for row in history if row.snapshot.get('answers', {}).get('q1') == '这个答案只对应旧问题']
        assert old and any(row.snapshot.get('questions', [{}])[0].get('question') == '实际处理结果是什么？' for row in old)


@pytest.mark.parametrize('current_change', ['disabled', 'revoke_consent'])
def test_runtime_settings_revoke_queued_cloud_authorization(database, monkeypatch, current_change):
    from app.services import ai_settings
    uid = make_entry(database)
    cloud_config = CONFIG | dict(provider='openai_compatible', model='cloud-model', base_url='https://example.invalid/v1', cloud_consent=True)
    queued = workflow.enqueue('entry:' + uid, cloud_config)
    current = cloud_config | ({'provider': 'disabled'} if current_change == 'disabled' else {'cloud_consent': False})
    monkeypatch.setattr(ai_settings, 'get_config', lambda: current)
    called = []

    def forbidden_provider(*args):
        called.append(True)
        return fake_provider(*args)

    workflow.process_draft(queued['uuid'], provider=forbidden_provider)
    assert not called
    assert read(database, AIDraft, queued['uuid'])['status'] == 'failed'


def test_new_sensitive_filter_applies_before_provider_is_called(database, monkeypatch):
    from app.services import ai_settings
    uid = make_entry(database, content='PRIVATE-MARKER', privacy_level=3)
    queued = workflow.enqueue('entry:' + uid, CONFIG | {'sensitivityExclude': False})
    monkeypatch.setattr(ai_settings, 'get_config', lambda: CONFIG | {'sensitivityExclude': True})
    called = []

    def forbidden_provider(*args):
        called.append(True)
        return fake_provider(*args)

    workflow.process_draft(queued['uuid'], provider=forbidden_provider)
    assert not called
    assert read(database, AIDraft, queued['uuid'])['status'] == 'failed'


def test_deleted_source_does_not_get_sent_after_queueing(database):
    uid = make_entry(database)
    queued = workflow.enqueue('entry:' + uid, CONFIG)
    with database.begin() as session:
        session.scalar(select(DailyEntry).where(DailyEntry.uuid == uid)).deleted_at = now()
    called = []

    def forbidden_provider(*args):
        called.append(True)
        return fake_provider(*args)

    workflow.process_draft(queued['uuid'], provider=forbidden_provider)
    assert not called
    assert read(database, AIDraft, queued['uuid'])['status'] == 'failed'


def test_daily_source_limit_counts_non_archive_sources(database):
    with database.begin() as session:
        session.add(DailyEntry(title='已有归档', content='归档文字', original_content='归档原文', date=TEST_DATE,
                              metadata_json={'ai_workflow_archive': True}))
        session.add_all(DailyEntry(title=str(index), content='来源', original_content='来源', date=TEST_DATE) for index in range(501))
    with pytest.raises(HTTPException) as error:
        workflow.enqueue('daily:' + TEST_DATE.isoformat(), CONFIG)
    assert error.value.status_code == 400


def test_attachment_evidence_and_mid_review_mutation_are_detected(database, tmp_path, monkeypatch):
    from app.services import backup
    uid = make_entry(database)
    attachment_uid = str(uuid4())
    path = tmp_path / 'source.txt'
    path.write_text('中文附件中的处理过程', encoding='utf-8')
    monkeypatch.setattr(backup, 'safe_attachment', lambda relative: path)
    with database.begin() as session:
        source = session.scalar(select(DailyEntry).where(DailyEntry.uuid == uid))
        session.add(Attachment(uuid=attachment_uid, entry_id=source.id, file_path='source.txt',
                               original_filename='source.txt', stored_filename='source.txt', file_size=path.stat().st_size,
                               sha256=backup.digest(path)))
        session.flush()
        session.add(AttachmentExtraction(attachment_uuid=attachment_uid, source_sha256=backup.digest(path), status='completed',
            text='中文附件中的处理过程', pages=[{'page': 2, 'start': 0, 'end': 10}]))

    draft = enqueue_and_generate(database, uid)
    assert any(evidence['attachment_uuid'] == attachment_uid and evidence['page'] == 2 for evidence in draft['evidence'])
    path.write_text('在审核期间变化的附件', encoding='utf-8')
    with pytest.raises(HTTPException) as error:
        workflow.approve(draft['uuid'], draft['revision'])
    assert error.value.status_code == 409


@pytest.mark.parametrize('setting,changed', [('provider', 'openai_compatible'), ('base_url', 'http://127.0.0.1:9999'), ('model', 'a-different-model')])
def test_changed_model_or_service_never_sends_old_snapshot(database, monkeypatch, setting, changed):
    from app.services import ai_settings
    uid = make_entry(database)
    queued = workflow.enqueue('entry:' + uid, CONFIG)
    monkeypatch.setattr(ai_settings, 'get_config', lambda: CONFIG | {setting: changed})
    calls = []

    def forbidden_provider(*args):
        calls.append(True)
        return fake_provider(*args)

    workflow.process_draft(queued['uuid'], provider=forbidden_provider)
    assert not calls and read(database, AIDraft, queued['uuid'])['status'] == 'failed'


def test_privacy_increase_propagates_to_existing_ai_archive(database):
    from app.schemas import EntryInput
    uid = make_entry(database)
    draft = enqueue_and_generate(database, uid)
    approved = workflow.approve(draft['uuid'], draft['revision'])
    assert read(database, DailyEntry, approved['archived_uuid'])['privacy_level'] == 1
    with database.begin() as session:
        source = session.scalar(select(DailyEntry).where(DailyEntry.uuid == uid))
        payload = {key: value for key, value in serialize(source).items() if key in EntryInput.model_fields}
        payload.update(privacy_level=3, tags=[], projects=[], people=[])
        save(session, 'entries', payload, uid)
    assert read(database, DailyEntry, approved['archived_uuid'])['privacy_level'] == 3
    with database.begin() as session:
        archive = session.scalar(select(DailyEntry).where(DailyEntry.uuid == approved['archived_uuid']))
        payload = {key: value for key, value in serialize(archive).items() if key in EntryInput.model_fields}
        payload.update(privacy_level=1, tags=[], projects=[], people=[])
        save(session, 'entries', payload, archive.uuid)
    assert read(database, DailyEntry, approved['archived_uuid'])['privacy_level'] == 3


def test_uncertain_and_brief_sources_are_questions_even_if_model_misses_them(database):
    uid = make_entry(database, content='可能明天交付，但日期未确认。')
    draft = enqueue_and_generate(database, uid)
    assert draft['status'] == 'pending_review' and draft['questions']
    assert uid in draft['questions'][0]['source_uuids']
    assert '不确定' in draft['questions'][0]['reason']
    with pytest.raises(HTTPException) as error:
        workflow.approve(draft['uuid'], draft['revision'])
    assert error.value.status_code == 400


def test_large_input_is_rejected_before_draft_is_persisted(database):
    uid = make_entry(database, content='事实' * 120000)
    with pytest.raises(HTTPException) as error:
        workflow.enqueue('entry:' + uid, CONFIG)
    assert error.value.status_code == 400
    with database() as session:
        assert session.scalar(select(func.count(AIDraft.id))) == 0


def test_model_may_not_omit_an_input_source(database):
    uid = make_entry(database)
    second_uid = make_entry(database, content='另一个独立事项，今天做了检查，留下完整过程与待办结果。')
    queued = workflow.enqueue('daily:' + TEST_DATE.isoformat(), CONFIG)

    def omitting_provider(items, *args):
        return fake_provider(items[:1], *args)

    workflow.process_draft(queued['uuid'], provider=omitting_provider)
    failed = read(database, AIDraft, queued['uuid'])
    assert failed['status'] == 'failed' and '遗漏' in failed['error']


def test_attachment_same_size_and_mtime_mutation_cannot_be_approved(database, tmp_path, monkeypatch):
    import os
    from app.services import backup
    uid = make_entry(database)
    attachment_uid = str(uuid4())
    path = tmp_path / 'bytes.txt'
    path.write_bytes(b'original')
    monkeypatch.setattr(backup, 'safe_attachment', lambda relative: path)
    with database.begin() as session:
        source = session.scalar(select(DailyEntry).where(DailyEntry.uuid == uid))
        session.add(Attachment(uuid=attachment_uid, entry_id=source.id, file_path='bytes.txt',
            original_filename='bytes.txt', stored_filename='bytes.txt', file_size=8, sha256=backup.digest(path)))
        session.flush()
        session.add(AttachmentExtraction(attachment_uuid=attachment_uid, source_sha256=backup.digest(path),
            status='completed', text='original', pages=[{'page': 1, 'start': 0, 'end': 8}]))
    draft = enqueue_and_generate(database, uid)
    stat = path.stat()
    path.write_bytes(b'modified')
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    with pytest.raises(HTTPException) as error:
        workflow.approve(draft['uuid'], draft['revision'])
    assert error.value.status_code == 409 and '字节' in error.value.detail


def test_mixed_future_and_uncertain_prose_never_looks_like_completed_facts(database):
    uid = make_entry(database, content='今天紧固后设备运行平稳。振动数值尚未确认，明天准备复测仪表并记录数值。')

    def mixed_provider(items, *args):
        result = fake_provider(items, *args)
        result['segments'][0]['section'] = '问题与风险'
        result['questions'] = [{'question': items[0]['text'], 'source_uuids': []}]
        result['summary'] = ''
        return result

    draft = enqueue_and_generate(database, uid, mixed_provider)
    assert '## 处理过程' in draft['content']
    assert '## 问题与风险' in draft['content']
    assert '## 后续计划' in draft['content']
    assert draft['summary'] and len(draft['questions']) == 1
