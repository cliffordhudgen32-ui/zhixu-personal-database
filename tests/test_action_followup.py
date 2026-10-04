"""Only an explicit manual confirmation may turn a written plan into a task."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from uuid import uuid4
import json
import zipfile

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app.action_routes import FollowupTaskRequest, router
from app.models import AuditLog, DailyEntry, Project, Task
from app.services import action_followup
from app.services.records import save
from app.workflow_models import AIDraft, AIArchive, AIReviewRevision
from test_ai_workflow import database, edit, enqueue_and_generate, make_entry, read


@pytest.fixture
def isolated(database, monkeypatch):
    from app.services import workflow
    monkeypatch.setattr(action_followup, 'transaction', workflow.transaction)
    return database


def plan(isolated, content='## 后续计划\n- 明天测量振动数值，结果尚不确定。\n- 复核设备支架。', **source_values):
    source = make_entry(isolated, **source_values)
    draft = enqueue_and_generate(isolated, source)
    draft = edit(isolated, draft, content=content)
    data = action_followup.candidate_options(draft['uuid'])
    return source, draft, data


def request(data, **values):
    return FollowupTaskRequest.model_validate({'revision': data['revision'], 'content_hash': data['content_hash'],
        'title': '人工确认：测量振动数值', 'confirmation': '创建待办', **values})


def test_candidates_use_explicit_sections_only_and_preserve_uncertainty():
    result = action_followup.parse_candidates('## 今日过程\n- 曾计划检查设备。\n'
        '## 三、后续计划\n- [ ] 尚未确认是否需要复测。\n  先核对原数据。\n'
        '- [x] 已做完的检查。\n  完成说明也不能变成待办。\n'
        '- 准备检查设备。\n### 现场安排\n- 联系现场人员。\n'
        '## 待核实问题\n- 有没有实际测量结果？')
    assert [item['source_text'] for item in result['items']] == [
        '尚未确认是否需要复测。\n先核对原数据。', '准备检查设备。', '联系现场人员。']


def test_plain_labels_code_blocks_and_duplicate_plans_are_handled_conservatively():
    result = action_followup.parse_candidates('**下一步：**\n- 联系现场人员。\n- 联系现场人员。\n'
        '```markdown\n## 后续计划\n- 代码例子不能创建待办。\n```\n'
        '> 引用中的计划不能作为待办。\n结果：\n- 此行不属于计划。\n'
        '后续安排：先确认是否需要复测。')
    assert [item['source_text'] for item in result['items']] == ['联系现场人员。', '先确认是否需要复测。']
    assert result['omitted'] == 0


def test_setext_headings_end_a_plan_before_a_different_section():
    result = action_followup.parse_candidates('后续计划\n--------\n- 先复测。\n'
        '完成结果\n--------\n已经核对参数。')
    assert [item['source_text'] for item in result['items']] == ['先复测。']


def test_limits_report_omissions_without_silently_shortening_plan_text():
    result = action_followup.parse_candidates('## 后续计划\n- ' + '长' * 10001 + '\n' +
        '\n'.join(f'- 第{i}项后续事项。' for i in range(102)))
    assert len(result['items']) == 100 and result['omitted'] == 3
    long_but_supported = action_followup.parse_candidates('## 后续计划\n- ' + '原句' * 300)['items'][0]
    assert len(long_but_supported['source_text']) == 600 and long_but_supported['title_truncated']
    assert len(long_but_supported['suggested_title']) == 500


def test_candidate_read_is_not_a_database_write(isolated):
    source, draft, data = plan(isolated)
    assert data['sources'][0]['uuid'] == source
    assert data['items'][0]['task'] is None
    original = read(isolated, DailyEntry, source)
    repeated = action_followup.candidate_options(draft['uuid'])
    assert repeated == data
    assert read(isolated, DailyEntry, source) == original
    assert read(isolated, AIDraft, draft['uuid']) == draft
    with isolated() as session:
        assert session.scalar(select(func.count()).select_from(Task)) == 0
        assert session.scalar(select(func.count()).select_from(AIArchive)) == 0


def test_manual_task_keeps_no_implicit_date_and_leaves_review_and_raw_record_unchanged(isolated):
    source, draft, data = plan(isolated)
    original = read(isolated, DailyEntry, source)
    result = action_followup.create_task(draft['uuid'], data['items'][0]['id'], request(data, source_uuid=source))
    task = result['task']
    assert result['created'] is True and task['status'] == '待办'
    assert task['due_date'] is None and task['completed_at'] is None
    assert task['origin'] == 'manual'
    provenance = task['metadata_json']['ai_followup']
    assert provenance['source_uuid'] == source and provenance['source_uuids'] == [source]
    assert provenance['candidate_text'] == '明天测量振动数值，结果尚不确定。'
    assert provenance['draft_uuid'] == draft['uuid'] and provenance['draft_revision'] == draft['revision']
    assert provenance['user_confirmed'] is True
    assert read(isolated, DailyEntry, source) == original
    assert read(isolated, AIDraft, draft['uuid']) == draft
    with isolated() as session:
        assert session.scalar(select(func.count()).select_from(AIArchive)) == 0


def test_manual_date_project_and_title_are_respected_and_privacy_is_inherited(isolated):
    source, draft, data = plan(isolated, privacy_level=2)
    with isolated.begin() as session:
        project = save(session, 'projects', {'name': '敏感演示项目', 'privacy_level': 3})
        project_uuid, project_id = project.uuid, project.id
    result = action_followup.create_task(draft['uuid'], data['items'][0]['id'], request(data,
        title=' 手工输入的实际待办 ', due_date='2030-01-10', project_uuid=project_uuid))
    task = result['task']
    assert task['title'] == '手工输入的实际待办'
    assert task['due_date'] == '2030-01-10' and task['project_id'] == project_id
    assert task['privacy_level'] == 3


@pytest.mark.parametrize('case', ['revision', 'hash', 'candidate', 'confirmation', 'title', 'source'])
def test_stale_and_invalid_manual_requests_never_create_a_task(isolated, case):
    source, draft, data = plan(isolated)
    values = {}
    identity = data['items'][0]['id']
    if case == 'revision':
        values['revision'] = data['revision'] - 1
    elif case == 'hash':
        values['content_hash'] = '0' * 64
    elif case == 'candidate':
        identity = 'f' * 64
    elif case == 'confirmation':
        values['confirmation'] = ''
    elif case == 'title':
        values['title'] = '  '
    elif case == 'source':
        values['source_uuid'] = make_entry(isolated, content='不属于这份草稿')
    with pytest.raises(HTTPException) as error:
        action_followup.create_task(draft['uuid'], identity, request(data, **values))
    assert error.value.status_code in (400, 409)
    with isolated() as session:
        assert session.scalar(select(func.count()).select_from(Task)) == 0


def test_repeat_click_and_retry_after_manual_task_edit_do_not_overwrite_task(isolated):
    source, draft, data = plan(isolated)
    payload = request(data)
    identity = data['items'][0]['id']
    first = action_followup.create_task(draft['uuid'], identity, payload)
    with isolated.begin() as session:
        original = first['task']
        manual = {key: value for key, value in original.items() if key in __import__('app.schemas', fromlist=['SCHEMAS']).SCHEMAS['tasks'].model_fields}
        manual.update(title='后来手动调整的任务标题', status='已完成', due_date='2030-01-11')
        save(session, 'tasks', manual, original['uuid'])
    edited = edit(isolated, draft, content='## 后续计划\n- 另一条新计划。')
    retry = action_followup.create_task(draft['uuid'], identity, payload)
    assert retry['created'] is False
    assert retry['task']['uuid'] == original['uuid'] and retry['task']['title'] == '后来手动调整的任务标题'
    assert retry['task']['status'] == '已完成' and retry['task']['due_date'] == '2030-01-11'
    assert read(isolated, AIDraft, draft['uuid']) == edited
    with isolated() as session:
        assert session.scalar(select(func.count()).select_from(Task)) == 1
        assert session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == 'ai_followup_create')) == 1


def test_normal_task_put_preserves_provenance_and_repeat_click_returns_edited_task(isolated, client, monkeypatch):
    from app import main
    from app.services import workflow
    monkeypatch.setattr(main, 'transaction', workflow.transaction)
    source, draft, data = plan(isolated, privacy_level=2)
    payload = request(data)
    first = action_followup.create_task(draft['uuid'], data['items'][0]['id'], payload)
    task = first['task']
    # The ordinary edit dialog historically sent just the visible task fields.
    result = client.put('/api/tasks/' + task['uuid'], json={'title': '通过普通编辑修改的待办标题',
        'description': '用户手动完善执行步骤。', 'status': '进行中', 'priority': 4,
        'due_date': '2030-01-12', 'privacy_level': 1})
    assert result.status_code == 200, result.text
    updated = result.json()
    assert updated['metadata_json']['ai_followup'] == task['metadata_json']['ai_followup']
    assert updated['privacy_level'] == 2
    repeated = action_followup.create_task(draft['uuid'], data['items'][0]['id'], payload)
    assert repeated['created'] is False and repeated['task']['uuid'] == task['uuid']
    assert repeated['task']['title'] == '通过普通编辑修改的待办标题'
    assert repeated['task']['status'] == '进行中' and repeated['task']['due_date'] == '2030-01-12'


@pytest.mark.parametrize('status', ['queued', 'running', 'rejected', 'failed'])
def test_unreviewable_draft_never_provides_new_candidates_or_creates_tasks(isolated, status):
    source, draft, data = plan(isolated)
    with isolated.begin() as session:
        session.scalar(select(AIDraft).where(AIDraft.uuid == draft['uuid'])).status = status
    with pytest.raises(HTTPException) as options_error:
        action_followup.candidate_options(draft['uuid'])
    with pytest.raises(HTTPException) as create_error:
        action_followup.create_task(draft['uuid'], data['items'][0]['id'], request(data))
    assert options_error.value.status_code == 409 and create_error.value.status_code == 409


def test_concurrent_duplicate_clicks_create_exactly_one_task(isolated):
    source, draft, data = plan(isolated)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: action_followup.create_task(draft['uuid'], data['items'][0]['id'], request(data)), range(6)))
    assert sum(item['created'] for item in results) == 1
    assert len({item['task']['uuid'] for item in results}) == 1


def test_unrelated_task_using_candidate_identity_is_not_overwritten(isolated):
    source, draft, data = plan(isolated)
    identity = action_followup.task_uuid(draft['uuid'], data['items'][0]['id'])
    with isolated.begin() as session:
        save(session, 'tasks', {'uuid': identity, 'title': '已有手工任务，不属于该计划'})
    with pytest.raises(HTTPException) as error:
        action_followup.create_task(draft['uuid'], data['items'][0]['id'], request(data))
    assert error.value.status_code == 409
    assert read(isolated, Task, identity)['title'] == '已有手工任务，不属于该计划'


def test_missing_or_trashed_sources_stop_new_task_but_keep_existing_task_retry(isolated):
    source, draft, data = plan(isolated)
    first = action_followup.create_task(draft['uuid'], data['items'][0]['id'], request(data))
    with isolated.begin() as session:
        session.scalar(select(DailyEntry).where(DailyEntry.uuid == source)).deleted_at = '2030-01-03T01:00:00'
    with pytest.raises(HTTPException) as error:
        action_followup.create_task(draft['uuid'], data['items'][1]['id'], request(data))
    assert error.value.status_code == 409
    assert action_followup.create_task(draft['uuid'], data['items'][0]['id'], request(data))['task']['uuid'] == first['task']['uuid']


def test_source_privacy_increase_after_draft_generation_applies_to_new_task(isolated):
    source, draft, data = plan(isolated)
    with isolated.begin() as session:
        session.scalar(select(DailyEntry).where(DailyEntry.uuid == source)).privacy_level = 3
    assert action_followup.create_task(draft['uuid'], data['items'][0]['id'], request(data))['task']['privacy_level'] == 3


@pytest.mark.parametrize('raised', ['source', 'project'])
def test_later_source_or_project_privacy_raise_protects_existing_task_and_ai_export(isolated, raised):
    from app.services import exporting
    source, draft, data = plan(isolated, content='## 后续计划\n- 核对仅用于隐私回归的事项-SENSITIVE-FOLLOWUP-991。')
    with isolated.begin() as session:
        project = save(session, 'projects', {'name': '关联演示项目'})
        project_uuid = project.uuid
    first = action_followup.create_task(draft['uuid'], data['items'][0]['id'], request(data,
        title='SENSITIVE-FOLLOWUP-TITLE-991', project_uuid=project_uuid))
    with isolated.begin() as session:
        if raised == 'source':
            save(session, 'entries', {'title': '后来升为敏感的原记录', 'content': '原记录保持在本机。',
                'date': date(2030, 1, 2), 'privacy_level': 3}, source)
        else:
            save(session, 'projects', {'name': '后来升为敏感的项目', 'privacy_level': 3}, project_uuid)
        task = session.scalar(select(Task).where(Task.uuid == first['task']['uuid']))
        assert task.privacy_level == 3
        # This small isolated fixture has no migration bookkeeping table yet.
        session.execute(text('CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) PRIMARY KEY)'))
        session.execute(text("INSERT INTO alembic_version (version_num) VALUES ('test-followup')"))
        path = exporting.export_records(session, {}, 'context', {})
        explicit_path = exporting.export_records(session, {}, 'context', {'include_sensitive': True})
    with zipfile.ZipFile(path) as archive:
        assert first['task']['uuid'] not in {item['uuid'] for item in json.loads(archive.read('tasks.json'))}
        assert all(b'SENSITIVE-FOLLOWUP' not in archive.read(name) for name in archive.namelist())
    with zipfile.ZipFile(explicit_path) as archive:
        assert first['task']['uuid'] in {item['uuid'] for item in json.loads(archive.read('tasks.json'))}


def test_approved_draft_can_create_pending_task_without_modifying_archive(isolated):
    source, draft, data = plan(isolated)
    from app.services import workflow
    approved = workflow.approve(draft['uuid'], draft['revision'])
    data = action_followup.candidate_options(draft['uuid'])
    archive_before = read(isolated, DailyEntry, approved['archived_uuid'])
    result = action_followup.create_task(draft['uuid'], data['items'][0]['id'], request(data))
    assert result['task']['status'] == '待办' and result['task']['metadata_json']['ai_followup']['archived_uuid'] == approved['archived_uuid']
    assert read(isolated, DailyEntry, approved['archived_uuid']) == archive_before
    assert read(isolated, AIDraft, draft['uuid']) == approved


def test_route_validates_uuid_date_and_candidate_identifier(isolated):
    source, draft, data = plan(isolated)
    application = FastAPI()
    application.include_router(router)
    with TestClient(application) as client:
        endpoint = f"/api/ai/drafts/{draft['uuid']}/followups/{data['items'][0]['id']}/task"
        payload = request(data).model_dump(mode='json')
        assert client.get(f"/api/ai/drafts/{draft['uuid']}/followups").status_code == 200
        assert client.post(endpoint, json=payload | {'due_date': 'tomorrow'}).status_code == 422
        assert client.post(endpoint, json=payload | {'project_uuid': 123}).status_code == 422
        assert client.post(endpoint, json=payload | {'status': '已完成'}).status_code == 422
        assert client.post(endpoint.replace(data['items'][0]['id'], 'wrong'), json=payload).status_code == 400
        first = client.post(endpoint, json=payload)
        assert first.status_code == 200 and first.json()['created'] is True
        second = client.post(endpoint, json=payload)
        assert second.status_code == 200 and second.json()['created'] is False
