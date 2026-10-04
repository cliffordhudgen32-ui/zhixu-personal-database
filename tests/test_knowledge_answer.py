import pytest
from fastapi import HTTPException

from app.models import DailyEntry
from app.services import knowledge_answer, workflow, ai_settings
from app.services.records import save
from test_ai_workflow import database, make_entry, read, CONFIG


@pytest.fixture
def answer_database(database, monkeypatch):
    monkeypatch.setattr(knowledge_answer, 'transaction', workflow.transaction)
    return database


def retrieve(database, uid):
    row = read(database, DailyEntry, uid)
    return lambda *args, **kwargs: dict(retrieval_method='semantic', warnings=[], items=[dict(
        uuid=uid, title=row['title'], date=row['date'], excerpt=row['content'][:320],
        field='content', attachment_uuid=None, page=None, score=0.8)])


def model(data, config, schema, prompt):
    source = data['references'][0]
    return {'answerable': True, 'statements': [{'text': '据记录：' + source['text'],
        'citations': [{'source_id': source['id'], 'quote': source['text']}]}], 'missing_information': ''}


def test_answer_has_quotes_and_never_edits_data(answer_database):
    uid = make_entry(answer_database)
    before = read(answer_database, DailyEntry, uid)
    result = knowledge_answer.answer('怎样处理问题？', provider=model, retriever=retrieve(answer_database, uid))
    assert result['answerable'] and result['sources'][0]['uuid'] == uid
    assert result['statements'][0]['citations'][0]['source_id'] == 'C1'
    assert read(answer_database, DailyEntry, uid) == before


@pytest.mark.parametrize('hidden', ['sensitive', 'outside_date', 'deleted'])
def test_invisible_candidates_never_reach_model(answer_database, hidden):
    uid = make_entry(answer_database, privacy_level=3 if hidden == 'sensitive' else 1)
    retriever = retrieve(answer_database, uid)
    if hidden == 'deleted':
        with answer_database.begin() as session:
            session.query(DailyEntry).filter_by(uuid=uid).one().deleted_at = '2030-01-03'
    result = knowledge_answer.answer('问题', {'start': '2040-01-01'} if hidden == 'outside_date' else {},
        provider=lambda *args: pytest.fail('Invisible source was sent'), retriever=retriever)
    assert not result['answerable'] and not result['sources']


def test_invented_citation_rejected(answer_database):
    uid = make_entry(answer_database)
    def bad(*args):
        response = model(*args)
        response['statements'][0]['citations'][0]['quote'] = '这个结果不在原文中'
        return response
    with pytest.raises(HTTPException) as failure:
        knowledge_answer.answer('问题', provider=bad, retriever=retrieve(answer_database, uid))
    assert failure.value.status_code == 422


def test_source_edit_during_answer_rejected(answer_database):
    uid = make_entry(answer_database)
    def changed(*args):
        with answer_database.begin() as session:
            save(session, 'entries', {'title': '新版本', 'content': '新的结果'}, uid)
        return model(*args)
    with pytest.raises(HTTPException) as failure:
        knowledge_answer.answer('问题', provider=changed, retriever=retrieve(answer_database, uid))
    assert failure.value.status_code == 409


def test_cloud_organize_consent_does_not_enable_qa(answer_database, monkeypatch):
    monkeypatch.setattr(ai_settings, 'get_config', lambda: dict(CONFIG, provider='openai_compatible', cloud_consent=True))
    with pytest.raises(HTTPException) as failure:
        knowledge_answer.answer('问题', provider=lambda *args: pytest.fail('Cloud should not be used'))
    assert failure.value.status_code == 409


def test_no_evidence_refuses_without_model(answer_database):
    result = knowledge_answer.answer('不存在的问题', retriever=lambda *args, **kwargs: {
        'items': [], 'retrieval_method': 'keyword', 'warnings': ['索引未准备']},
        provider=lambda *args: pytest.fail('No evidence reached model'))
    assert not result['answerable'] and result['retrieval_method'] == 'keyword'
