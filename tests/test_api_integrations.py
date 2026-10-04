from pathlib import Path
import os
import subprocess
import sys
import pytest
from app.database import engine
from app.utils.process_lock import process_guard

def test_ai_conversation_shared_memory(client,create):
    project=create('projects',name='对话关联项目')
    conversation=create('conversations',platform='本地测试AI',conversation_title='对话共享资产测试',
        user_message='原始问题',assistant_message='原始回答',tags=['对话测试'],project_id=project['id'])
    assert conversation['entry_uuid']==conversation['uuid']
    envelope=client.get('/api/entries/'+conversation['uuid']).json()
    assert envelope['item_type']=='conversations'
    assert envelope['tags'][0]['name']=='对话测试'
    assert envelope['projects'][0]['uuid']==project['uuid']
    assert client.post('/api/entries/'+conversation['uuid']+'/attachments',files={'file':('对话附件.txt',b'conversation-file')}).status_code==200
    result=client.get('/api/conversations/'+conversation['uuid']).json()
    assert result['attachments']
    result=client.put('/api/conversations/'+conversation['uuid'],json={
        'platform':'本地测试AI','conversation_title':'对话共享资产测试','user_message':'修改问题','assistant_message':'新回答','tags':['对话测试'],'project_id':project['id']})
    assert result.status_code==200,result.text
    envelope=client.get('/api/entries/'+conversation['uuid']).json()
    assert '原始问题' in envelope['original_content']
    assert client.get('/api/items/'+conversation['uuid']+'/history').json()['revisions']
    assert client.delete('/api/conversations/'+conversation['uuid']).status_code==200
    assert not any(x['uuid']==conversation['uuid'] for x in client.get('/api/conversations').json()['items'])
    assert client.post('/api/entries/'+conversation['uuid']+'/restore').status_code==200
    assert any(x['uuid']==conversation['uuid'] for x in client.get('/api/conversations').json()['items'])

def test_asset_flags_and_ghost_protection(client,create):
    knowledge=create('knowledge',title='收藏完整知识',details={'maturity_level':5,'source_title':'来源资料'},content='正文')
    response=client.patch('/api/items/'+knowledge['uuid']+'/flags',json={'is_favorite':True,'is_archived':True})
    assert response.status_code==200,response.text
    assert response.json()['details']['maturity_level']==5
    assert response.json()['is_favorite']
    assert client.post('/api/entries',json={'title':'不能创建幽灵知识','item_type':'knowledge'}).status_code in (400,422)
    assert client.post('/api/entries',json={'title':'不能创建幽灵对话','item_type':'conversations'}).status_code in (400,422)

def test_project_review_and_reference_pagination(client,create):
    project=create('projects',name='项目复盘测试')
    review=client.post('/api/projects/'+project['uuid']+'/review',json={'summary':'人工项目复盘','lessons':'改进方法'})
    assert review.status_code==200,review.text
    assert review.json()['project_uuid']==project['uuid']
    found=client.get('/api/projects',params={'internal_id':project['id']}).json()
    assert found['total']==1 and found['items'][0]['uuid']==project['uuid']
    for n in range(5):create('tasks',title=f'分页任务{n}',project_id=project['id'])
    overview=client.get('/api/projects/'+project['uuid']+'/overview',params={'page':2,'size':2}).json()
    assert len(overview['tasks'])==2 and overview['totals']['tasks']==5
    assert client.get('/api/tasks',params={'project_id':project['id']}).json()['total']==5

def test_single_writer_process_lock(client):
    with pytest.raises(RuntimeError):
        with process_guard(engine):
            pass

def test_offline_api_docs(client):
    docs=client.get('/docs')
    assert docs.status_code==200
    assert '/static/vendor/swagger-ui-bundle.js' in docs.text
    assert 'cdn.jsdelivr' not in docs.text
    assert client.get('/static/vendor/swagger-ui-bundle.js').status_code==200

def test_migration_retains_existing_conversations(tmp_path):
    script='''
from pathlib import Path
from contextlib import closing
import sqlite3
from alembic import command
from alembic.config import Config
from app.config import ROOT
from app.services.backup import db_path,backup_before_upgrade
from app.database import migrate
cfg=Config(str(ROOT/'alembic.ini'))
cfg.set_main_option('script_location',str(ROOT/'database'))
command.upgrade(cfg,'03ae2f1407ba')
with closing(sqlite3.connect(db_path())) as conn:
    conn.execute("INSERT INTO ai_conversations(platform,conversation_title,conversation_date,user_message,assistant_message,summary,tags,project_id,source_file,privacy_level,id,uuid,created_at,updated_at,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ('TestAI','Old conversation','2026-10-01','Original user','Original answer','Summary','[]',None,'',2,1,'ca546943-a868-4ffb-9d60-35b53a8920fb','2026-10-01T00:00:00+00:00','2026-10-01T00:00:00+00:00','{}'))
    conn.commit()
before=backup_before_upgrade()
assert before is not None
migrate()
with closing(sqlite3.connect(db_path())) as conn:
    assert conn.execute('SELECT entry_uuid FROM ai_conversations').fetchone()[0]=='ca546943-a868-4ffb-9d60-35b53a8920fb'
    assert 'Original user' in conn.execute('SELECT original_content FROM entries').fetchone()[0]
    assert not conn.execute('PRAGMA foreign_key_check').fetchall()
print('migration retained old conversation')
'''
    env=dict(os.environ,PLD_HOME=str(tmp_path),DATABASE_URL='sqlite:///'+(tmp_path/'data/personal.db').as_posix(),PYTHONUTF8='1')
    result=subprocess.run([sys.executable,'-c',script],env=env,capture_output=True,text=True,encoding='utf-8',timeout=60)
    assert result.returncode==0,result.stdout+result.stderr
