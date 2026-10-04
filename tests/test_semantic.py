"""Vector pipeline tests use a deterministic injected embedder, never a fake UI result."""
import math
import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.extraction_models import AttachmentExtraction
from app.models import Attachment, DailyEntry
from app.semantic_models import EmbeddingChunk
from app.services import semantic
from app.services.knowledge import save_asset
from app.services.records import save


class TestEmbedder:
    __test__ = False
    model_name = 'test-concept-encoder'
    model_version = 'test-concept-encoder-v1'

    def __init__(self, callback=None):
        self.callback = callback
        self.calls = 0

    def embed(self, texts, **kwargs):
        self.calls += 1
        if self.callback:
            self.callback()
        for text in texts:
            # Explicitly injected test embeddings let tests assert semantic ranking
            # and safety independent of package installation/model network access.
            mechanical = any(word in text for word in ('机器', '设备', '故障', '振动', '轴承'))
            finance = any(word in text for word in ('投资', '股票', '风险', '资金'))
            yield [float(mechanical), float(finance), 0.1]

    query_embed = embed


@pytest.fixture
def database(tmp_path):
    engine = create_engine('sqlite:///' + (tmp_path / 'semantic.db').as_posix())

    @event.listens_for(engine, 'connect')
    def configure(connection, record):
        connection.execute('PRAGMA foreign_keys=ON')

    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def make_entry(database, title, content='', **values):
    with database.begin() as session:
        return save(session, 'entries', dict(title=title, content=content, **values)).uuid


def test_semantic_ranking_uses_vectors_and_returns_evidence(database):
    mechanical = make_entry(database, '排查记录', '轴承磨损会导致设备持续异常振动')
    financial = make_entry(database, '资金计划', '股票投资需要控制资金风险')
    embedder = TestEmbedder()
    semantic.index_all(embedder=embedder, session_factory=database)
    result = semantic.search('机器故障', embedder=embedder, session_factory=database)
    assert result['retrieval_method'] == 'semantic'
    assert result['items'][0]['uuid'] == mechanical
    assert '轴承' in result['items'][0]['excerpt']
    assert result['items'][0]['score'] > 0.9 and result['score_metric'] == 'cosine_similarity'
    assert financial not in {item['uuid'] for item in result['items']}


def test_vectors_are_normalized_float32_and_rebuildable(database):
    uid = make_entry(database, '机器故障', '设备轴承振动')
    embedder = TestEmbedder()
    outcome = semantic.index_entry(uid, embedder=embedder, session_factory=database)
    assert outcome['state'] == 'indexed'
    with database() as session:
        chunks = list(session.scalars(select(EmbeddingChunk)))
        assert chunks and all(chunk.dimension == 3 and len(chunk.vector) == 12 for chunk in chunks)
        assert all(abs(sum(value * value for value in semantic._unpack(chunk.vector)) - 1) < 1e-6 for chunk in chunks)
    assert semantic.index_entry(uid, embedder=embedder, session_factory=database)['state'] == 'unchanged'
    assert semantic.index_entry(uid, embedder=embedder, session_factory=database, force=True)['state'] == 'indexed'
    with database() as session:
        assert session.scalar(select(func.count(EmbeddingChunk.id))) == outcome['chunks']


def test_privacy_deleted_and_stale_entries_are_filtered(database):
    public = make_entry(database, '公开机器记录', '轴承异常振动')
    private = make_entry(database, '敏感机器记录', '设备故障', privacy_level=3)
    deleted = make_entry(database, '删除机器记录', '设备故障')
    changed = make_entry(database, '更新机器记录', '设备故障')
    embedder = TestEmbedder()
    semantic.index_all(embedder=embedder, session_factory=database)
    with database.begin() as session:
        session.scalar(select(DailyEntry).where(DailyEntry.uuid == deleted)).deleted_at = '2026-10-03T00:00:00+00:00'
        session.scalar(select(DailyEntry).where(DailyEntry.uuid == changed)).content = '新的正文'
    result = semantic.search('机器故障', embedder=embedder, session_factory=database)
    assert {item['uuid'] for item in result['items']} == {public}
    result = semantic.search('机器故障', include_sensitive=True, embedder=embedder, session_factory=database)
    assert {item['uuid'] for item in result['items']} == {public, private}


def test_revision_change_during_inference_does_not_commit_stale_vectors(database):
    uid = make_entry(database, '机器检查', '旧设备故障正文')

    def change_source():
        with database.begin() as session:
            session.scalar(select(DailyEntry).where(DailyEntry.uuid == uid)).content = '后来编辑的正文'

    result = semantic.index_entry(uid, embedder=TestEmbedder(change_source), session_factory=database)
    assert result['state'] == 'stale'
    with database() as session:
        assert session.scalar(select(func.count(EmbeddingChunk.id))) == 0
        assert session.scalar(select(DailyEntry).where(DailyEntry.uuid == uid)).original_content == '旧设备故障正文'


def test_structured_fields_and_manual_normalized_prose_are_indexed(database):
    with database.begin() as session:
        item = save_asset(session, 'cases', {'title': '案例', 'normalized_content': '用户整理：设备振动检查顺序',
            'details': {'problem': '机器轴承故障', 'solution': '更换轴承'}})
        uid = item.uuid
    semantic.index_entry(uid, embedder=TestEmbedder(), session_factory=database)
    with database() as session:
        chunks = list(session.scalars(select(EmbeddingChunk).where(EmbeddingChunk.entry_uuid == uid)))
        assert any(chunk.field == 'details.problem' and chunk.evidence == '机器轴承故障' for chunk in chunks)
        normalized = [chunk.evidence for chunk in chunks if chunk.field == 'normalized_content']
        assert normalized == ['用户整理：设备振动检查顺序']
        assert not any('pld-structured-fields' in chunk.evidence for chunk in chunks)


def test_attachment_page_evidence_and_delete_are_respected(database):
    uid = make_entry(database, '来源资料')
    attachment_uuid = str(uuid4())
    with database.begin() as session:
        entry = session.scalar(select(DailyEntry).where(DailyEntry.uuid == uid))
        session.add(Attachment(uuid=attachment_uuid, original_filename='检修报告.pdf', stored_filename='file.pdf',
            file_path='2026/10/file.pdf', file_size=10, sha256='a' * 64, entry_id=entry.id))
        session.flush()
        session.add(AttachmentExtraction(attachment_uuid=attachment_uuid, source_sha256='a' * 64, status='partial',
            text='普通说明\n机器轴承振动导致故障', pages=[{'page': 1, 'start': 0, 'end': 4}, {'page': 7, 'start': 5, 'end': 17}],
            truncated=True))
    embedder = TestEmbedder()
    result = semantic.index_entry(uid, embedder=embedder, session_factory=database)
    assert result['warnings']
    result = semantic.search('设备故障', embedder=embedder, session_factory=database)
    assert result['items'][0]['page'] == 7 and result['items'][0]['attachment_uuid'] == attachment_uuid
    assert result['items'][0]['source_label'] == '检修报告.pdf'
    with database.begin() as session:
        session.delete(session.scalar(select(Attachment).where(Attachment.uuid == attachment_uuid)))
    result = semantic.search('设备故障', embedder=embedder, session_factory=database)
    assert not result['items']


def test_missing_model_and_missing_index_are_labelled_keyword(database, monkeypatch):
    make_entry(database, '机器故障关键词', '公开正文')
    make_entry(database, '机器故障敏感', '秘密', privacy_level=3)

    def missing():
        raise semantic.SemanticUnavailable('未准备模型')

    monkeypatch.setattr(semantic, 'get_embedder', missing)
    result = semantic.search('机器故障', session_factory=database)
    assert result['retrieval_method'] == 'keyword' and result['warnings'] == ['未准备模型']
    assert len(result['items']) == 1 and result['items'][0]['score'] is None
    result = semantic.search('机器故障', embedder=TestEmbedder(), session_factory=database)
    assert result['retrieval_method'] == 'keyword' and '索引' in result['warnings'][0]


def test_model_versions_cannot_be_mixed(database):
    uid = make_entry(database, '机器故障', '检查设备振动')
    semantic.index_entry(uid, embedder=TestEmbedder(), session_factory=database)
    changed_model = TestEmbedder()
    changed_model.model_version = 'different-weights-v2'
    result = semantic.search('机器故障', embedder=changed_model, session_factory=database)
    assert result['retrieval_method'] == 'keyword'


def test_bad_vector_does_not_erase_working_index(database):
    uid = make_entry(database, '机器故障', '检查设备振动')
    semantic.index_entry(uid, embedder=TestEmbedder(), session_factory=database)

    class Invalid(TestEmbedder):
        def embed(self, texts, **kwargs):
            for text in texts:
                yield [math.nan, 0, 1]

    with pytest.raises(ValueError):
        semantic.index_entry(uid, embedder=Invalid(), session_factory=database, force=True)
    with database() as session:
        assert session.scalar(select(func.count(EmbeddingChunk.id))) > 0


def test_long_chunks_preserve_exact_unicode_offsets():
    value = '第一段。' + '机器设备检修说明' * 200
    chunks = list(semantic._split(value))
    assert len(chunks) > 2
    assert all(value[start:end] == evidence and len(evidence) <= 320 for start, end, evidence in chunks)
    assert chunks[0][0] == 0 and chunks[-1][1] == len(value)


def test_inference_is_outside_database_lock(database):
    uid = make_entry(database, '机器故障', '检查设备振动')
    from app.database import LOCK

    def assert_no_lock():
        assert not LOCK._is_owned()

    semantic.index_entry(uid, embedder=TestEmbedder(assert_no_lock), session_factory=database)


def test_automatic_queue_coalesces_updates_and_preserves_backpressure(monkeypatch):
    from app import semantic_routes
    monkeypatch.delenv('PLD_WORKERS_DISABLED', raising=False)

    class DeferredExecutor:
        def __init__(self):
            self.calls = []

        def submit(self, operation):
            self.calls.append(operation)

    executor = DeferredExecutor()
    monkeypatch.setattr(semantic_routes, '_EXECUTOR', executor)
    monkeypatch.setattr(semantic_routes, '_JOBS', {})
    monkeypatch.setattr(semantic_routes, '_SUBMITTER', None)
    monkeypatch.setattr(semantic_routes, '_DEFERRED_ALL', False)
    monkeypatch.setattr(semantic, '_MODEL', object())
    monkeypatch.setattr(semantic, 'index_entry', lambda *args, **kwargs: {'state': 'indexed'})
    first_uuid = str(uuid4())
    semantic_routes.enqueue_index(first_uuid)
    assert semantic_routes.enqueue_index(first_uuid)['coalesced']
    assert len(executor.calls) == 1
    for index in range(9):
        semantic_routes.enqueue_index(str(uuid4()))
    assert semantic_routes.enqueue_index(str(uuid4()))['state'] == 'deferred'
    assert len(executor.calls) == 10
    executor.calls[0]()
    assert len(executor.calls) == 11
    assert any(job['state'] == 'queued' and job['entry_uuid'] is None for job in semantic_routes._JOBS.values())


def test_automatic_queue_is_quiet_before_model_preparation(monkeypatch):
    from app import semantic_routes
    monkeypatch.setattr(semantic, '_MODEL', None)
    monkeypatch.setattr(semantic, '_STATE', 'not_ready')
    monkeypatch.setattr(semantic, '_manifest', lambda: None)
    assert semantic_routes.enqueue_index(str(uuid4()))['state'] == 'awaiting_model'


@pytest.mark.skipif(os.getenv('PLD_TEST_REAL_SEMANTIC') != '1', reason='需显式启用已准备的真实中文模型验收')
def test_real_chinese_model_ranks_synonym_without_exact_keyword(database, monkeypatch):
    cache = os.getenv('PLD_REAL_MODEL_DIRECTORY')
    if cache:
        monkeypatch.setattr(semantic, 'MODEL_CACHE', Path(cache).resolve())
        monkeypatch.setattr(semantic, 'MANIFEST', Path(cache).resolve() / 'semantic_model.json')
    semantic.prepare_model(allow_download=False)
    correct = make_entry(database, '资料 A', '设备持续出现异常振动时，先检查轴承磨损、润滑状态和转动部件是否松动。')
    make_entry(database, '资料 B', '室内盆栽需要适当浇水，并按照季节调整光照和肥料。')
    make_entry(database, '资料 C', '投资基金之前应了解费用、收益和承担亏损的能力。')
    semantic.index_all(session_factory=database)
    result = semantic.search('机器发抖该怎么排查', min_score=-1, session_factory=database)
    assert result['retrieval_method'] == 'semantic' and result['items'][0]['uuid'] == correct
    assert result['items'][0]['score'] > 0.35
    with database() as session:
        assert session.scalar(select(EmbeddingChunk.dimension).limit(1)) == 512
