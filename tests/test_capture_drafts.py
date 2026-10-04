"""Draft safety, explicit submission, concurrency, and upgrade preservation."""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import os
import json
import subprocess
import sys
from threading import RLock
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from app.capture_models import CaptureDraft
from app.capture_routes import router
from app.database import Base
from app.models import AuditLog, DailyEntry, Project
from app.services import capture
from app.services.records import query_entries, save
from app.services.workflow import source_snapshot


@pytest.fixture
def capture_db(tmp_path, monkeypatch):
    engine = create_engine('sqlite:///' + (tmp_path / 'capture.db').as_posix(),
                           connect_args={'check_same_thread': False})

    @event.listens_for(engine, 'connect')
    def configure(connection, record):
        connection.execute('PRAGMA foreign_keys=ON')

    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    lock = RLock()

    @contextmanager
    def isolated_transaction():
        with lock, factory.begin() as session:
            yield session

    monkeypatch.setattr(capture, 'transaction', isolated_transaction)
    yield factory
    engine.dispose()


@pytest.fixture
def capture_client(capture_db):
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        yield client


def put(client, uid=None, revision=0, **values):
    uid = uid or str(uuid4())
    response = client.put('/api/capture-drafts/' + uid, json={
        'revision': revision, 'date': '2026-10-04', 'title': '未确认的文字',
        'content': '原始草稿：设备检查完毕，参数尚待核实。', **values,
    })
    assert response.status_code == 200, response.text
    return response.json()


def post(client, draft, action, revision=None):
    return client.post('/api/capture-drafts/' + draft['uuid'] + '/' + action,
                       json={'revision': draft['revision'] if revision is None else revision})


def test_autosaved_unicode_draft_stays_out_of_sources_search_and_entries(capture_client, capture_db):
    body = '草稿包含中文、emoji 🍁、换行\n### 待人工确认\n暂时不要整理此文字。'
    draft = put(capture_client, content=body, title='', privacy_level=3)
    assert draft['revision'] == 1 and draft['status'] == 'open'
    assert draft['content'] == body and draft['privacy_level'] == 3
    assert capture_client.get('/api/capture-drafts/' + draft['uuid']).json() == draft
    with capture_db() as session:
        assert session.scalar(select(func.count()).select_from(DailyEntry)) == 0
        assert list(session.scalars(query_entries({'q': '草稿'}))) == []
        assert source_snapshot(session, 'daily:2026-10-04', {'sensitivityExclude': False}) == []
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 0


def test_conflicting_edit_does_not_overwrite_newer_text(capture_client):
    first = put(capture_client)
    latest = put(capture_client, first['uuid'], first['revision'], content='第二窗口先保存的更新')
    conflict = capture_client.put('/api/capture-drafts/' + first['uuid'], json={
        'revision': first['revision'], 'date': first['date'], 'content': '旧窗口文字',
    })
    assert conflict.status_code == 409
    assert capture_client.get('/api/capture-drafts/' + first['uuid']).json() == latest


def test_list_is_bounded_and_does_not_return_full_body(capture_client):
    drafts = [put(capture_client, content='很长的草稿🍁' * 1000) for _ in range(3)]
    discarded = post(capture_client, drafts[1], 'discard').json()
    page = capture_client.get('/api/capture-drafts', params={'page': 1, 'size': 2}).json()
    assert page['total'] == 2 and len(page['items']) == 2
    assert page['counts'] == {'open': 2, 'discarded': 1, 'submitted': 0}
    assert all('content' not in item and len(item['content_preview']) == 280
               and item['content_length'] == 6000 for item in page['items'])
    trash = capture_client.get('/api/capture-drafts', params={'status': 'discarded'}).json()
    assert trash['items'][0]['uuid'] == discarded['uuid']
    assert capture_client.get('/api/capture-drafts', params={'size': 51}).status_code == 422
    assert capture_client.get('/api/capture-drafts', params={'status': 'unknown'}).status_code == 422


def test_soft_discard_restore_preserves_prose_and_requires_current_revision(capture_client, capture_db):
    draft = put(capture_client)
    response = post(capture_client, draft, 'discard')
    assert response.status_code == 200
    discarded = response.json()
    assert discarded['status'] == 'discarded' and discarded['discarded_at']
    assert discarded['revision'] == draft['revision'] + 1
    assert post(capture_client, draft, 'restore').status_code == 409
    assert post(capture_client, discarded, 'submit').status_code == 409
    response = capture_client.put('/api/capture-drafts/' + draft['uuid'], json={
        'revision': discarded['revision'], 'date': draft['date'], 'content': '不能覆盖回收站',
    })
    assert response.status_code == 409
    restored = post(capture_client, discarded, 'restore').json()
    assert restored['status'] == 'open' and restored['discarded_at'] is None
    assert restored['content'] == draft['content'] and restored['revision'] == discarded['revision'] + 1
    with capture_db() as session:
        assert session.scalar(select(func.count()).select_from(CaptureDraft)) == 1
        assert session.scalar(select(func.count()).select_from(DailyEntry)) == 0


def test_explicit_submit_preserves_original_and_uses_permanent_uuid(capture_client, capture_db):
    draft = put(capture_client, title='', content='\n 第一行作为标题\n原始记录第二行。',
                privacy_level=2, template_key='work')
    response = post(capture_client, draft, 'submit')
    assert response.status_code == 200, response.text
    result = response.json()
    entry = result['entry']
    assert entry['uuid'] == draft['uuid'] == result['draft']['submitted_entry_uuid']
    assert entry['title'] == '第一行作为标题'
    assert entry['item_type'] == 'inbox' and entry['content'] == entry['original_content'] == draft['content']
    assert not entry['generated_by_ai'] and entry['origin'] == 'manual' and entry['privacy_level'] == 2
    assert entry['metadata_json']['capture_template'] == 'work'
    assert result['draft']['status'] == 'submitted' and result['draft']['submitted_at']
    assert result['draft']['revision'] == draft['revision'] + 1 and result['reused'] is False
    assert capture_client.get('/api/capture-drafts').json()['total'] == 0
    with capture_db() as session:
        assert source_snapshot(session, 'daily:2026-10-04', {'sensitivityExclude': False})[0]['uuid'] == draft['uuid']


def test_submit_retry_does_not_overwrite_later_edits_or_recreate_deleted_entry(capture_client, capture_db):
    draft = put(capture_client)
    first = post(capture_client, draft, 'submit').json()
    with capture_db.begin() as session:
        entry = session.scalar(select(DailyEntry).where(DailyEntry.uuid == draft['uuid']))
        entry.content = '人工随后修改了记录，此内容必须保留。'
        entry.title = '随后修改的标题'
        entry.privacy_level = 3
        original = entry.original_content
        audits = session.scalar(select(func.count()).select_from(AuditLog))
    repeated = post(capture_client, draft, 'submit').json()
    assert repeated['reused'] is True and repeated['draft'] == first['draft']
    assert repeated['entry']['content'] == '人工随后修改了记录，此内容必须保留。'
    assert repeated['entry']['title'] == '随后修改的标题' and repeated['entry']['privacy_level'] == 3
    assert repeated['entry']['original_content'] == original
    with capture_db.begin() as session:
        assert session.scalar(select(func.count()).select_from(AuditLog)) == audits
        assert session.scalar(select(func.count()).select_from(DailyEntry)) == 1
        session.delete(session.scalar(select(DailyEntry).where(DailyEntry.uuid == draft['uuid'])))
    response = post(capture_client, draft, 'submit')
    assert response.status_code == 410
    with capture_db() as session:
        assert session.scalar(select(func.count()).select_from(DailyEntry)) == 0
        saved = session.scalar(select(CaptureDraft).where(CaptureDraft.uuid == draft['uuid']))
        assert saved.status == 'submitted' and saved.submitted_entry_uuid is None


def test_stale_submit_and_blank_submit_leave_draft_open(capture_client, capture_db):
    first = put(capture_client)
    latest = put(capture_client, first['uuid'], first['revision'], content='\n \t')
    assert post(capture_client, first, 'submit').status_code == 409
    assert post(capture_client, latest, 'submit').status_code == 400
    assert capture_client.get('/api/capture-drafts/' + latest['uuid']).json() == latest
    with capture_db() as session:
        assert session.scalar(select(func.count()).select_from(DailyEntry)) == 0


def test_submitted_draft_cannot_be_edited_discarded_or_restored(capture_client):
    draft = put(capture_client)
    submitted = post(capture_client, draft, 'submit').json()['draft']
    response = capture_client.put('/api/capture-drafts/' + draft['uuid'], json={
        'revision': submitted['revision'], 'date': draft['date'], 'content': '不能通过草稿修改已保存记录',
    })
    assert response.status_code == 409
    assert post(capture_client, submitted, 'discard').status_code == 409
    assert post(capture_client, submitted, 'restore').status_code == 409


def test_submit_transaction_failure_rolls_back_entry_and_status(capture_client, capture_db, monkeypatch):
    draft = put(capture_client)
    original_save = capture.save

    def fail_after_entry(session, kind, payload):
        original_save(session, kind, payload)
        raise RuntimeError('simulate the process failing before draft confirmation')

    monkeypatch.setattr(capture, 'save', fail_after_entry)
    with pytest.raises(RuntimeError):
        capture.submit_draft(draft['uuid'], draft['revision'])
    assert capture.get_draft(draft['uuid']) == draft
    with capture_db() as session:
        assert session.scalar(select(func.count()).select_from(DailyEntry)) == 0
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 0


def test_concurrent_submit_creates_one_entry(capture_client, capture_db):
    draft = put(capture_client)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: capture.submit_draft(draft['uuid'], draft['revision']), range(16)))
    assert sum(not result['reused'] for result in results) == 1
    assert {result['entry']['uuid'] for result in results} == {draft['uuid']}
    with capture_db() as session:
        assert session.scalar(select(func.count()).select_from(DailyEntry)) == 1


def test_uuid_collision_never_adopts_or_overwrites_existing_record(capture_client, capture_db):
    draft = put(capture_client)
    with capture_db.begin() as session:
        save(session, 'entries', {'uuid': draft['uuid'], 'title': '先存在的真实记录', 'content': '保持不变'})
    assert post(capture_client, draft, 'submit').status_code == 409
    with capture_db() as session:
        entry = session.scalar(select(DailyEntry).where(DailyEntry.uuid == draft['uuid']))
        assert entry.content == '保持不变'
    identity = str(uuid4())
    with capture_db.begin() as session:
        save(session, 'entries', {'uuid': identity, 'title': '其他真实记录'})
    response = capture_client.put('/api/capture-drafts/' + identity, json={'revision': 0, 'date': draft['date']})
    assert response.status_code == 409


def test_project_validation_and_deleted_project_prevent_partial_submit(capture_client, capture_db):
    with capture_db.begin() as session:
        project = save(session, 'projects', {'name': '关联项目'})
        project_uid = project.uuid
    draft = put(capture_client, project_ids=[project_uid, project_uid])
    assert draft['project_ids'] == [project_uid]
    with capture_db.begin() as session:
        session.delete(session.scalar(select(Project).where(Project.uuid == project_uid)))
    assert post(capture_client, draft, 'submit').status_code == 400
    with capture_db() as session:
        assert session.scalar(select(func.count()).select_from(DailyEntry)) == 0
    assert capture.get_draft(draft['uuid']) == draft
    valid = put(capture_client)
    response = capture_client.put('/api/capture-drafts/' + valid['uuid'], json={
        'revision': valid['revision'], 'date': valid['date'], 'content': '不能部分保存',
        'project_ids': [str(uuid4())],
    })
    assert response.status_code == 400 and capture.get_draft(valid['uuid']) == valid


@pytest.mark.parametrize('field,value', [
    ('revision', True), ('revision', -1), ('title', '长' * 501), ('content', '长' * 2_000_001),
    ('template_key', '长' * 51), ('privacy_level', 4), ('privacy_level', True),
    ('project_ids', ['invalid']), ('unrecognized', '不接受未知字段'), ('date', '2026-13-45'),
], ids=['revision-bool', 'revision-negative', 'title-limit', 'content-limit', 'template-limit',
         'privacy-limit', 'privacy-bool', 'project-format', 'unknown-field', 'invalid-date'])
def test_invalid_payload_does_not_create_a_draft(capture_client, capture_db, field, value):
    response = capture_client.put('/api/capture-drafts/' + str(uuid4()), json={
        'revision': 0, 'date': '2026-10-04', field: value,
    })
    assert response.status_code == 422
    with capture_db() as session:
        assert session.scalar(select(func.count()).select_from(CaptureDraft)) == 0


def test_missing_or_invalid_identity_does_not_create_ghosts(capture_client):
    assert capture_client.get('/api/capture-drafts/' + str(uuid4())).status_code == 404
    assert capture_client.get('/api/capture-drafts/not-a-uuid').status_code == 422
    assert capture_client.delete('/api/capture-drafts/' + str(uuid4())).status_code == 405
    response = capture_client.put('/api/capture-drafts/' + str(uuid4()),
                                  json={'revision': 8, 'date': '2026-10-04'})
    assert response.status_code == 409


def test_full_json_keeps_all_draft_states_and_record_importer_refuses_it(capture_client, capture_db, tmp_path, monkeypatch):
    from app.services import exporting, importing
    monkeypatch.setattr(exporting, 'HOME', tmp_path)
    with capture_db.begin() as session:
        project_uid = save(session, 'projects', {'name': '完整迁移关联项目'}).uuid
    open_draft = put(capture_client, content='未提交的敏感中文草稿🍁', privacy_level=3,
                     project_ids=[project_uid], template_key='equipment')
    discarded = post(capture_client, put(capture_client), 'discard').json()
    submitted = post(capture_client, put(capture_client), 'submit').json()['draft']
    with capture_db.begin() as session:
        artifact = exporting.export_database(session, 'json')
    document = json.loads(artifact.read_text(encoding='utf-8'))
    assert set(document['tables']) == set(Base.metadata.tables)
    rows = {item['uuid']: item for item in document['tables']['capture_drafts']}
    assert set(rows) == {open_draft['uuid'], discarded['uuid'], submitted['uuid']}
    assert rows[open_draft['uuid']]['content'] == open_draft['content']
    assert rows[open_draft['uuid']]['privacy_level'] == 3
    assert rows[open_draft['uuid']]['project_ids'] == [project_uid]
    assert rows[open_draft['uuid']]['template_key'] == 'equipment'
    assert rows[discarded['uuid']]['status'] == 'discarded' and rows[discarded['uuid']]['discarded_at']
    assert rows[submitted['uuid']]['status'] == 'submitted'
    assert rows[submitted['uuid']]['submitted_entry_uuid'] == document['tables']['entries'][0]['uuid']
    # The existing importer is intentionally for record arrays, not arbitrary
    # normalized SQL tables. Refusing a full snapshot prevents silent draft loss.
    with pytest.raises(HTTPException) as error:
        importing.parse('full.json', artifact.read_bytes(), {})
    assert error.value.status_code == 400 and '完整恢复请使用 ZIP 备份' in error.value.detail
    with capture_db() as session:
        assert session.scalar(select(func.count()).select_from(CaptureDraft)) == 3
        assert session.scalar(select(func.count()).select_from(DailyEntry)) == 1


def test_capture_upgrade_preserves_old_records_and_full_backup_restore(tmp_path):
    # This process gets its own disk data home before importing app.config.
    script = '''
from contextlib import closing
import sqlite3
from uuid import uuid4
import zipfile
from alembic import command
from alembic.config import Config
from app.config import ROOT
from app.database import migrate, transaction, engine
from app.models import DailyEntry
from app.services.records import save
from app.services import backup, capture
from app.services.capture import CaptureInput
cfg = Config(str(ROOT / 'alembic.ini'))
cfg.set_main_option('script_location', str(ROOT / 'database'))
command.upgrade(cfg, '20261003_workflow')
with transaction() as s:
    uid = save(s, 'entries', {'title': '升级前原始记录', 'content': '升级前原文不能改变'}).uuid
with closing(sqlite3.connect(backup.db_path())) as c:
    before = c.execute('SELECT * FROM entries WHERE uuid=?', (uid,)).fetchone()
    assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='capture_drafts'").fetchone()
pre_upgrade = backup.backup_before_upgrade()
assert pre_upgrade and pre_upgrade.is_file()
migrate()
with closing(sqlite3.connect(backup.db_path())) as c:
    assert c.execute('SELECT * FROM entries WHERE uuid=?', (uid,)).fetchone() == before
    assert c.execute('SELECT version_num FROM alembic_version').fetchone()[0] == '20261004_capture'
    assert c.execute('SELECT count(*) FROM capture_drafts').fetchone()[0] == 0
identity = str(uuid4())
draft = capture.save_draft(identity, CaptureInput(revision=0, date='2026-10-04', content='未确认的文字备份'))
source = backup.create_backup()
with zipfile.ZipFile(source) as archive:
    assert 'personal.db' in archive.namelist()
capture.discard_draft(identity, draft['revision'])
assert capture.get_draft(identity)['status'] == 'discarded'
assert backup.restore_backup(source)['ok']
assert capture.get_draft(identity) == draft
with closing(sqlite3.connect(backup.db_path())) as c:
    assert c.execute('SELECT * FROM entries WHERE uuid=?', (uid,)).fetchone() == before
    assert c.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
    assert not c.execute('PRAGMA foreign_key_check').fetchall()
engine.dispose()
print('old records retained; capture drafts backed up and restored')
'''
    env = dict(os.environ, PLD_HOME=str(tmp_path),
               DATABASE_URL='sqlite:///' + (tmp_path / 'data/personal.db').as_posix(), PYTHONUTF8='1')
    result = subprocess.run([sys.executable, '-c', script], env=env, capture_output=True,
                            text=True, encoding='utf-8', timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'capture drafts backed up and restored' in result.stdout
