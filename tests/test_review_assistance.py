"""A rewrite is a suggestion, never an edit or an approval."""
import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.services import review_assistance, workflow
from app.services.records import save
from app.workflow_models import AIDraft, AIArchive, AIReviewRevision
from app.models import DailyEntry
from app.database import LOCK
from test_ai_workflow import database, make_entry, enqueue_and_generate, read, edit


def proposal(items, config, schema, prompt):
    assert not LOCK._is_owned()
    first = items['references'][0]
    return {'proposal': '优化表达：' + items['review_selection'],
            'evidence': [{key: first[key] for key in ('source_uuid', 'attachment_uuid', 'page')}
                         | {'quote': first['text']}], 'warnings': []}


def test_rewrite_is_read_only_and_citations_are_checkable(database):
    uid = make_entry(database)
    draft = enqueue_and_generate(database, uid)
    original = read(database, DailyEntry, uid)
    result = review_assistance.suggest_rewrite(draft['uuid'], draft['revision'], draft['content'], '', proposal)
    assert result['original'] == draft['content'] and result['source_evidence'][0]['source_uuid'] == uid
    assert read(database, AIDraft, draft['uuid']) == draft
    assert read(database, DailyEntry, uid) == original
    with database() as session:
        assert session.scalar(select(func.count()).select_from(AIArchive)) == 0
        assert session.scalar(select(func.count()).select_from(AIReviewRevision)) == 1


@pytest.mark.parametrize('kind', ['revision', 'selection', 'source'])
def test_stale_inputs_never_reach_model(database, kind):
    uid = make_entry(database)
    draft = enqueue_and_generate(database, uid)
    revision, selection = draft['revision'], draft['content']
    if kind == 'revision':
        revision -= 1
    elif kind == 'selection':
        selection = '这段文字不在审核稿中'
    else:
        with database.begin() as session:
            save(session, 'entries', {'title': '后来修改', 'content': '来源改变'}, uid)
    with pytest.raises(HTTPException) as error:
        review_assistance.suggest_rewrite(draft['uuid'], revision, selection, '',
            lambda *args: pytest.fail('Stale data reached a model'))
    assert error.value.status_code == 409


def test_invalid_quote_is_not_offered(database):
    uid = make_entry(database)
    draft = enqueue_and_generate(database, uid)
    def invented(*args):
        result = proposal(*args)
        result['evidence'][0]['quote'] = '原文不存在的完成结果'
        return result
    with pytest.raises(HTTPException) as error:
        review_assistance.suggest_rewrite(draft['uuid'], draft['revision'], draft['content'], '', invented)
    assert error.value.status_code == 422
    assert read(database, AIDraft, draft['uuid']) == draft


def test_edit_during_inference_invalidates_suggestion(database):
    uid = make_entry(database)
    draft = enqueue_and_generate(database, uid)
    def concurrent(*args):
        edit(database, draft, content='人工新修改不会被文字建议覆盖')
        return proposal(*args)
    with pytest.raises(HTTPException) as error:
        review_assistance.suggest_rewrite(draft['uuid'], draft['revision'], draft['content'], '', concurrent)
    assert error.value.status_code == 409
    assert read(database, AIDraft, draft['uuid'])['content'] == '人工新修改不会被文字建议覆盖'


def test_rewrite_route_rejects_oversized_input_without_inference(client):
    result = client.post('/api/ai/drafts/missing/rewrite', json={
        'revision': 1, 'selection': '字' * 4001})
    assert result.status_code == 422
