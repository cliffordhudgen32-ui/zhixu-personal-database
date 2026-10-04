"""Regression checks for portable identities, original data and AI export switches."""
import csv
import io
import json
import zipfile
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.knowledge_models import Case, Domain, Experience, Topic, KnowledgeRelation, ReuseLog, Revision
from app.models import DailyEntry, Project, Review, AIConversation, Attachment
from app.services import importing, exporting
from app.services.knowledge import save_asset
from app.services.records import save, serialize


@pytest.fixture
def databases(tmp_path):
    engines = []

    def create_database(name):
        engine = create_engine('sqlite:///' + (tmp_path / (name + '.db')).as_posix())
        engines.append(engine)

        @event.listens_for(engine, 'connect')
        def foreign_keys(connection, record):
            connection.execute('PRAGMA foreign_keys=ON')

        Base.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(text('CREATE TABLE alembic_version (version_num TEXT NOT NULL)'))
            connection.execute(text("INSERT INTO alembic_version VALUES ('test')"))
        return sessionmaker(engine, expire_on_commit=False)

    yield create_database
    for engine in engines:
        engine.dispose()


def read_export(client, payload):
    response = client.post('/api/export', json=payload)
    assert response.status_code == 200, response.text
    return zipfile.ZipFile(io.BytesIO(client.get(response.json()['url']).content))


def test_taxonomy_and_links_cross_database(databases):
    source = databases('source')
    destination = databases('destination')
    with source.begin() as session:
        parent = save(session, 'domains', {'name': '上级领域', 'description': '上级说明'})
        domain = save(session, 'domains', {'name': '目标领域', 'parent_id': parent.id})
        topic_parent = save(session, 'topics', {'name': '上级主题', 'domain_id': domain.id})
        topic = save(session, 'topics', {'name': '目标主题', 'parent_id': topic_parent.id, 'domain_id': domain.id})
        project = save(session, 'projects', {'name': '跨库项目'})
        person = save(session, 'people', {'name': '跨库人物'})
        item = save_asset(session, 'cases', {'title': '跨库案例', 'content': '最初原文', 'tags': ['跨库'],
            'projects': [project.uuid], 'people': [person.uuid], 'details': {'domain_id': domain.id, 'topic_id': topic.id, 'lessons': '经验'}})
        from app.models import Link
        link = Link(entry_id=item.id, url='https://example.com/source', title='证据')
        session.add(link)
        session.flush()
        identities = dict(item=item.uuid, domain=domain.uuid, parent=parent.uuid, topic=topic.uuid, topic_parent=topic_parent.uuid,
                          project=project.uuid, person=person.uuid, link=link.uuid)
        path = exporting.export_records(session, {}, 'jsonl', {})
    with zipfile.ZipFile(path) as archive:
        raw = archive.read('entries.jsonl')
    with destination.begin() as session:
        for index in range(4):
            save(session, 'domains', {'name': '本地已有领域 ' + str(index)})
            save(session, 'topics', {'name': '本地已有主题 ' + str(index)})
        result = importing.preview(session, 'items.jsonl', raw, {})
        assert not result['errors'], result
        assert importing.confirm(session, result['token'], 'skip')['imported'] == 1
        item = session.scalar(select(DailyEntry).where(DailyEntry.uuid == identities['item']))
        data = serialize(item)
        domain = session.get(Domain, data['details']['domain_id'])
        topic = session.get(Topic, data['details']['topic_id'])
        assert domain.uuid == identities['domain']
        assert session.get(Domain, domain.parent_id).uuid == identities['parent']
        assert topic.uuid == identities['topic'] and topic.domain_id == domain.id
        assert session.get(Topic, topic.parent_id).uuid == identities['topic_parent']
        assert data['projects'][0]['uuid'] == identities['project']
        assert data['people'][0]['uuid'] == identities['person']
        assert data['links'][0]['uuid'] == identities['link']
        assert item.original_content == '最初原文'


def test_new_import_remaps_experience_source(databases):
    database = databases('case_reference')
    case_uuid, experience_uuid = str(uuid4()), str(uuid4())
    rows = [
        {'uuid': experience_uuid, 'item_type': 'experiences', 'title': '来自案例的经验', 'details': {'source_case_uuid': case_uuid}},
        {'uuid': case_uuid, 'item_type': 'cases', 'title': '原案例', 'details': {'result': '成功'}},
    ]
    with database.begin() as session:
        preview = importing.preview(session, 'items.json', json.dumps(rows).encode(), {})
        assert not preview['errors']
        assert importing.confirm(session, preview['token'], 'new')['imported'] == 2
        experience = session.scalar(select(Experience))
        case = session.scalar(select(Case))
        assert experience.source_case_uuid == case.uuid
        assert case.uuid != case_uuid and experience.uuid != experience_uuid


def test_mixed_record_and_asset_csv(client, create):
    tag = 'mixed-' + uuid4().hex
    create(title='普通记录', tags=[tag])
    create('cases', title='结构化案例', tags=[tag], details={'problem': '具体问题'})
    archive = read_export(client, {'format': 'csv', 'filters': {'tag': tag}})
    rows = list(csv.DictReader(io.StringIO(archive.read('entries.csv').decode('utf-8-sig'))))
    assert len(rows) == 2
    assert {row['item_type'] for row in rows} == {'entries', 'cases'}
    preview = client.post('/api/import/preview', files={'file': ('mixed.csv', archive.read('entries.csv'))}).json()
    assert not preview['errors'], preview


def test_export_body_switch_covers_every_channel(client, create):
    marker = 'body-' + uuid4().hex
    tag = 'safety-' + uuid4().hex
    project = create('projects', name='安全项目', description=marker, summary=marker, metadata_json={'extra': marker})
    person = create('people', name='安全人物', contact_note=marker, notes=marker, metadata_json={'extra': marker})
    first = create('cases', title='普通案例', content=marker, summary=marker, notes=marker,
                   metadata_json={'extra': marker}, tags=[tag], projects=[project['uuid']], people=[person['uuid']],
                   details={'lessons': marker})
    second = create('knowledge', title='普通知识', tags=[tag])
    assert client.post('/api/item-relations', json={'from_uuid': first['uuid'], 'to_uuid': second['uuid'],
        'description': marker, 'judgment': marker, 'evidence': marker}).status_code == 200
    assert client.post('/api/cases/' + first['uuid'] + '/review', json={'lessons': marker, 'summary': marker}).status_code == 200
    assert client.post('/api/items/' + first['uuid'] + '/reuse', json={'result': marker, 'notes': marker}).status_code == 200
    assert client.post('/api/entries/' + first['uuid'] + '/links', json={'url': 'https://example.com', 'description': marker}).status_code == 200
    archive = read_export(client, {'format': 'context', 'filters': {'tag': tag},
        'options': {'include_content': False, 'include_summary': False, 'include_attachments': False}})
    assert marker.encode() not in b''.join(archive.read(name) for name in archive.namelist())


def test_sensitive_reuse_project_is_excluded(client, create):
    marker = 'private-' + uuid4().hex
    tag = 'reuse-' + uuid4().hex
    project = create('projects', name=marker, privacy_level=3)
    item = create('knowledge', title='公开知识', tags=[tag])
    assert client.post('/api/items/' + item['uuid'] + '/reuse', json={'project_id': project['id'], 'notes': marker}).status_code == 200
    archive = read_export(client, {'format': 'context', 'filters': {'tag': tag}})
    assert marker.encode() not in b''.join(archive.read(name) for name in archive.namelist())


def test_non_boolean_sensitive_option_is_rejected(client):
    response = client.post('/api/export', json={'format': 'context', 'options': {'include_sensitive': 'false'}})
    assert response.status_code == 400


def test_partial_export_overwrite_preserves_omitted_data(client, create):
    tag = 'partial-' + uuid4().hex
    item = create('solutions', title='部分导出', content='正文永久保留', summary='摘要永久保留', tags=[tag],
                  details={'steps': '结构化步骤永久保留', 'confidence': 'verified'})
    archive = read_export(client, {'format': 'context', 'filters': {'tag': tag},
        'options': {'include_content': False, 'include_summary': False}})
    preview = client.post('/api/import/preview', files={'file': ('entries.jsonl', archive.read('entries.jsonl'))}).json()
    assert not preview['errors'], preview
    response = client.post('/api/import/confirm', json={'token': preview['token'], 'strategy': 'overwrite'})
    assert response.status_code == 200, response.text
    data = client.get('/api/solutions/' + item['uuid']).json()
    assert data['content'] == '正文永久保留' and data['summary'] == '摘要永久保留'
    assert data['details']['steps'] == '结构化步骤永久保留' and data['original_content'] == '正文永久保留'


def test_stale_preview_cannot_overwrite_new_edit(client, create):
    item = create(title='并发预览', content='旧正文')
    preview = client.post('/api/import/preview', files={'file': ('entry.json', json.dumps(item).encode())}).json()
    assert client.put('/api/entries/' + item['uuid'], json={'title': '并发预览', 'content': '后来编辑'}).status_code == 200
    response = client.post('/api/import/confirm', json={'token': preview['token'], 'strategy': 'overwrite'})
    assert response.status_code == 409
    assert client.get('/api/entries/' + item['uuid']).json()['content'] == '后来编辑'


@pytest.mark.parametrize('details', [{'maturity_level': 10}, {'confidence': 'unsupported'}, {'difficulty': -1}])
def test_preview_validates_structured_fields(client, details):
    raw = json.dumps([{'item_type': 'knowledge', 'title': '无效字段', 'details': details}]).encode()
    response = client.post('/api/import/preview', files={'file': ('items.json', raw)})
    assert response.status_code == 200
    assert response.json()['valid_count'] == 0 and response.json()['errors']


def test_generic_foreign_keys_and_uuid_collision(client, create):
    assert client.post('/api/topics', json={'name': '无效领域', 'domain_id': 999999999}).status_code == 400
    assert client.post('/api/reviews', json={'date': date.today().isoformat(), 'target_uuid': str(uuid4())}).status_code == 400
    item = create(title='UUID 冲突')
    assert client.post('/api/entries', json={'title': 'UUID 冲突', 'uuid': item['uuid']}).status_code == 409
    assert client.post('/api/entries', json={'title': '幽灵资产', 'item_type': 'knowledge'}).status_code == 400
    assert client.post('/api/entries', json={'title': '幽灵对话', 'item_type': 'conversations'}).status_code == 400


def test_batch_export_does_not_load_each_asset_individually(databases):
    database = databases('batch')
    with database.begin() as session:
        for index in range(80):
            save_asset(session, 'knowledge', {'title': '批量知识 ' + str(index), 'details': {'confidence': 'high'}})
    queries = []
    engine = database.kw['bind']

    @event.listens_for(engine, 'before_cursor_execute')
    def count_queries(connection, cursor, statement, parameters, context, executemany):
        queries.append(statement)

    with database.begin() as session:
        rows = list(exporting.export_entry_batches(session, select(DailyEntry).order_by(DailyEntry.id)))
    assert len(rows) == 80
    assert sum('FROM knowledge ' in statement or 'FROM knowledge\n' in statement for statement in queries) == 1
    assert len(queries) < 20


def test_conversation_uses_shared_envelope_and_preserves_original(client, create):
    project = create('projects', name='对话项目')
    conversation = create('conversations', platform='测试平台', conversation_title='对话共享资产',
        user_message='最初提问', assistant_message='最初回复', tags=['对话共享'], project_id=project['id'])
    uid = conversation['uuid']
    envelope = client.get('/api/entries/' + uid).json()
    assert envelope['item_type'] == 'conversations'
    assert envelope['tags'][0]['name'] == '对话共享' and envelope['projects'][0]['uuid'] == project['uuid']
    attachment = client.post('/api/entries/' + uid + '/attachments', files={'file': ('conversation.txt', b'data')}).json()
    assert client.get('/api/conversations/' + uid).json()['attachments'][0]['uuid'] == attachment['uuid']
    response = client.put('/api/conversations/' + uid, json={'platform': '测试平台', 'conversation_title': '对话共享资产',
        'user_message': '更新提问', 'assistant_message': '更新回复', 'tags': ['更新标签'], 'project_id': project['id']})
    assert response.status_code == 200, response.text
    envelope = client.get('/api/entries/' + uid).json()
    assert '最初提问' in envelope['original_content'] and '更新提问' in envelope['content']
    assert client.get('/api/items/' + uid + '/history').json()['revisions']


def test_conversation_record_export_import(databases):
    source = databases('conversation_source')
    destination = databases('conversation_destination')
    with source.begin() as session:
        item = save(session, 'conversations', {'platform': '测试平台', 'conversation_title': '完整对话',
            'user_message': '用户提问', 'assistant_message': '助手回复', 'tags': ['对话'], 'privacy_level': 2})
        uid = item.uuid
        path = exporting.export_records(session, {}, 'jsonl', {})
    with zipfile.ZipFile(path) as archive:
        raw = archive.read('entries.jsonl')
    with destination.begin() as session:
        result = importing.preview(session, 'conversations.jsonl', raw, {})
        assert not result['errors'], result
        assert importing.confirm(session, result['token'], 'skip')['imported'] == 1
        item = session.scalar(select(AIConversation).where(AIConversation.uuid == uid))
        entry = session.scalar(select(DailyEntry).where(DailyEntry.uuid == uid))
        assert item.entry_uuid == entry.uuid == uid
        assert item.user_message == '用户提问' and item.assistant_message == '助手回复'
        assert '用户提问' in entry.original_content and entry.tags[0].name == '对话'


def test_csv_formula_escape_and_literal_apostrophe_are_reversible():
    text_value = "标题,正文\n普通标题,'literal\n公式标题,'=1+1\n重复前缀,''literal\n"
    rows = importing.parse('entries.csv', text_value.encode(), {})
    assert rows[0]['正文'] == "'literal"
    assert rows[1]['正文'] == '=1+1'
    assert rows[2]['正文'] == "'literal"
    for value in ['=1+1', '+cmd', '-cmd', '@SUM(A1)', "'literal", '普通文本']:
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=['title'])
        writer.writeheader()
        writer.writerow({'title': exporting.safe_csv(value)})
        assert importing.parse('records.csv', output.getvalue().encode(), {})[0]['title'] == value


def test_normalized_prose_survives_structured_updates(client, create):
    item = create('cases', title='整理正文保护', normalized_content='用户整理正文必须保留',
                  details={'problem': '旧派生问题'})
    from tests.test_core import body
    payload = body(item)
    payload['details'] = {'problem': '新派生问题'}
    response = client.put('/api/cases/' + item['uuid'], json=payload)
    assert response.status_code == 200, response.text
    value = response.json()['normalized_content']
    assert '用户整理正文必须保留' in value and '新派生问题' in value
    assert '旧派生问题' not in value


def test_body_switch_can_keep_attachment_names(client, create):
    tag = 'attachment-' + uuid4().hex
    marker = 'attachment-body-' + uuid4().hex
    item = create(title='只导出附件名称', tags=[tag])
    response = client.post('/api/entries/' + item['uuid'] + '/attachments',
        files={'file': ('keep-name.txt', b'data')}, data={'description': marker})
    assert response.status_code == 200, response.text
    archive = read_export(client, {'format': 'context', 'filters': {'tag': tag}, 'options': {'include_content': False}})
    raw = b''.join(archive.read(name) for name in archive.namelist())
    assert b'keep-name.txt' in raw and marker.encode() not in raw


def test_context_export_contains_project_reviews_and_case_associations(client, create):
    tag = 'context-links-' + uuid4().hex
    project = create('projects', name='项目复盘导出')
    case = create('cases', title='案例关联导出', tags=[tag], projects=[project['uuid']])
    task = create('tasks', title='独立关联的案例任务')
    event_item = create('events', title='独立关联的案例事件')
    for kind, item in [('tasks', task), ('events', event_item)]:
        assert client.post('/api/cases/' + case['uuid'] + '/associate', json={'kind': kind, 'uuid': item['uuid']}).status_code == 200
    review = create('reviews', date=date.today().isoformat(), review_type='project', project_uuid=project['uuid'], summary='项目复盘原文')
    archive = read_export(client, {'format': 'context', 'filters': {'tag': tag}})
    assert task['uuid'] in {item['uuid'] for item in json.loads(archive.read('tasks.json'))}
    assert event_item['uuid'] in {item['uuid'] for item in json.loads(archive.read('events.json'))}
    assert json.loads(archive.read('case_tasks.jsonl')) == {'case_uuid': case['uuid'], 'task_uuid': task['uuid']}
    assert json.loads(archive.read('case_events.jsonl')) == {'case_uuid': case['uuid'], 'event_uuid': event_item['uuid']}
    reviews = [json.loads(line) for line in archive.read('reviews.jsonl').splitlines()]
    assert review['uuid'] in {item['uuid'] for item in reviews}
    assert '结构化信息'.encode() in archive.read('context.md')


def test_unfiltered_context_contains_unassociated_tasks(client, create):
    task = create('tasks', title='未关联项目人物案例的任务')
    archive = read_export(client, {'format': 'context'})
    assert task['uuid'] in {item['uuid'] for item in json.loads(archive.read('tasks.json'))}
