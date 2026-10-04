import io
import json
import sqlite3
import zipfile
from contextlib import closing
from datetime import date
from pathlib import Path
from app.database import engine
from app.services.backup import db_path

def body(entry):
    from app.schemas import EntryInput
    result={k:v for k,v in entry.items() if k in EntryInput.model_fields}
    for key in ('projects','people'):
        result[key]=[x['uuid'] for x in result.get(key,[])]
    result['tags']=[x['name'] for x in result.get('tags',[])]
    return result

def test_database(client):
    assert client.get('/').status_code==200
    assert client.get('/openapi.json').status_code==200
    with closing(sqlite3.connect(db_path())) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert len(db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())>=30

def test_crud_links_search_recycle(client,create):
    p=create('projects',name='测试项目')
    person=create('people',name='测试同事')
    e=create(title='焊接气孔 排查',content='检查焊接参数与材料缺陷',tags=['焊接','经验'],projects=[p['uuid']],people=[person['uuid']])
    uid=e['uuid']
    assert len(e['tags'])==2 and e['projects'][0]['uuid']==p['uuid']
    for q in ['焊接 缺陷','焊接气孔','" OR *','% _']:
        r=client.get('/api/search',params={'q':q})
        assert r.status_code==200,r.text
    assert client.get('/api/search',params={'q':'焊接 缺陷','project':p['uuid'],'person':person['uuid'],'tag':'经验'}).json()['total']==1
    data=body(e);data['content']='已处理缺陷';data['is_favorite']=True
    assert client.put('/api/entries/'+uid,json=data).status_code==200
    assert client.get('/api/entries/'+uid).json()['original_content']=='检查焊接参数与材料缺陷'
    assert client.delete('/api/entries/'+uid).status_code==200
    assert client.get('/api/search',params={'q':'焊接气孔'}).json()['total']==0
    assert any(x['uuid']==uid for x in client.get('/api/entries?trash=true').json()['items'])
    assert client.post('/api/entries/'+uid+'/restore').status_code==200
    assert client.get('/api/search',params={'q':'焊接气孔'}).json()['total']==1
    assert client.get('/api/projects/'+p['uuid']+'/overview').json()['entries']['total']==1

def test_attachments_security(client,create):
    e=create(title='附件安全')
    r=client.post('/api/entries/'+e['uuid']+'/attachments',files={'file':('../../秘密.txt','附件中文'.encode(),'text/plain')})
    assert r.status_code==200,r.text
    a=r.json();assert '..' not in a['file_path'] and a['original_filename']=='秘密.txt'
    assert client.get('/api/attachments/'+a['uuid']).content=='附件中文'.encode()
    assert client.post('/api/entries/'+e['uuid']+'/links',json={'url':'javascript:alert(1)'}).status_code==400
    assert client.post('/api/entries/'+e['uuid']+'/links',json={'url':'https://example.com','title':'资料'}).status_code==200
    assert client.delete('/api/attachments/'+a['uuid']).status_code==200
    html=client.post('/api/markdown',json={'content':'<script>alert(1)</script><img src=x onerror="alert(1)">\n\n|a|b|\n|-|-|\n|1|2|'}).json()['html']
    assert '<script>' not in html and 'onerror' not in html and '<table>' in html
    assert client.post('/api/entries',json={'title':'CSRF'},headers={'x-local-token':''}).status_code==403
    assert client.get('/api/session',headers={'origin':'https://evil.test'}).status_code==403
    assert client.get('/api/session',headers={'host':'evil.test'}).status_code==400

def test_exports(client,create):
    public=create(title='导出公开',content='普通内容',tags=['导出测试'])
    private=create(title='导出敏感标记',content='secret-xyz',privacy_level=3,tags=['导出测试'])
    for fmt in ['json','jsonl','csv','markdown','txt','context']:
        r=client.post('/api/export',json={'format':fmt,'filters':{'tag':'导出测试'},'options':{'ai':True}})
        assert r.status_code==200,r.text
        z=zipfile.ZipFile(io.BytesIO(client.get(r.json()['url']).content))
        assert 'README_AI.md' in z.namelist() and 'manifest.json' in z.namelist()
        assert json.loads(z.read('manifest.json'))['record_count']==1
        assert b'secret-xyz' not in b''.join(z.read(n) for n in z.namelist())
    for fmt in ['db','sql','full-json']:
        r=client.post('/api/export',json={'format':fmt})
        assert r.status_code==200,r.text
        raw=client.get(r.json()['url']).content
        assert len(raw)>100
        if fmt=='full-json':
            assert 'entries' in json.loads(raw)['tables']

def test_import_duplicate(client,create):
    e=create(title='导入重复',content='内容')
    raw=json.dumps([e],ensure_ascii=False).encode()
    r=client.post('/api/import/preview',files={'file':('entries.json',raw)})
    assert r.status_code==200,r.text
    p=r.json();assert p['duplicates']==1 and not p['errors']
    count=client.get('/api/entries').json()['total']
    r=client.post('/api/import/confirm',json={'token':p['token'],'strategy':'skip'})
    assert r.status_code==200,r.text
    assert r.json()['skipped']==1
    assert client.get('/api/entries').json()['total']==count
    p=client.post('/api/import/preview',files={'file':('x.jsonl',(json.dumps(e)+'\n').encode())}).json()
    assert client.post('/api/import/confirm',json={'token':p['token'],'strategy':'new'}).json()['imported']==1
    p=client.post('/api/import/preview',files={'file':('x.csv','标题,正文\n映射测试,正文'.encode())},data={'mapping':'{"标题":"title","正文":"content"}'}).json()
    assert not p['errors']
    assert client.post('/api/import/confirm',json={'token':p['token']}).status_code==200

def test_backup_restore(client,create):
    e=create(title='恢复前记录',content='original')
    a=client.post('/api/entries/'+e['uuid']+'/attachments',files={'file':('backup.txt',b'backup-body')}).json()
    r=client.post('/api/backups');assert r.status_code==200,r.text
    name=r.json()['name']
    e['content']='changed'
    assert client.put('/api/entries/'+e['uuid'],json=body(e)).status_code==200
    r=client.post('/api/backup-restore',json={'name':name,'confirmation':'恢复备份'})
    assert r.status_code==200,r.text
    assert 'before_restore' in r.json()['safety_backup']
    assert client.get('/api/entries/'+e['uuid']).json()['content']=='original'
    assert client.get('/api/attachments/'+a['uuid']).content==b'backup-body'
    result=client.post('/api/integrity').json()
    assert result['ok'],result
    engine.dispose()
    assert client.get('/api/entries/'+e['uuid']).json()['title']=='恢复前记录'

def test_bad_backup_no_change(client,create):
    count=client.get('/api/entries').json()['total']
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w') as z:
        z.writestr('../attack','no')
    assert client.post('/api/backup-upload',files={'file':('bad.zip',buf.getvalue())}).status_code==400
    assert client.get('/api/entries').json()['total']==count

def test_tasks_stats_calendar(client,create):
    t=create('tasks',title='今日任务',due_date=date.today().isoformat())
    data={'title':t['title'],'status':'已完成','due_date':t['due_date']}
    assert client.put('/api/tasks/'+t['uuid'],json=data).json()['completed_at']
    assert client.get('/api/stats').status_code==200
    assert client.get('/api/stats').json()['completed_tasks']>=1
    assert client.get('/api/calendar',params={'month':date.today().strftime('%Y-%m')}).status_code==200
    assert client.get('/api/timeline').status_code==200

def test_knowledge_workflow(client,create):
    d=create('domains',name='跨行业测试')
    k=create('knowledge',title='知识验证标题',content='不可覆盖的原文',details={'domain_id':d['id'],'maturity_level':4,'confidence':'high'})
    assert k['details']['maturity_level']==4
    data=body(k);data['details']={'domain_id':d['id'],'maturity_level':5,'confidence':'verified'};data['content']='更新认识';data['revision_reason']='实践后修订'
    r=client.put('/api/knowledge/'+k['uuid'],json=data);assert r.status_code==200,r.text
    assert r.json()['original_content']=='不可覆盖的原文'
    assert len(client.get('/api/items/'+k['uuid']+'/history').json()['revisions'])==1
    assert client.post('/api/items/'+k['uuid']+'/reuse',json={'result':'有效'}).json()['times_used']==1
    assert client.post('/api/knowledge/'+k['uuid']+'/reviewed',json={'next_review_date':'2026-12-01'}).json()['review_count']==1
    c=create('cases',title='案例测试',details={'domain_id':d['id'],'problem':'结构化特殊检索词','outcome':'成功'})
    result=client.get('/api/global-search',params={'q':'结构化特殊检索词'})
    assert result.status_code==200,result.text
    assert result.json()['cases']['total']==1
    r=client.post('/api/item-relations',json={'from_uuid':k['uuid'],'to_uuid':c['uuid'],'relation_type':'case_of'})
    assert r.status_code==200,r.text
    assert client.get('/api/items/'+k['uuid']+'/relations').json()[0]['target']['uuid']==c['uuid']
    assert client.post('/api/cases/'+c['uuid']+'/review',json={'lessons':'保留经验'}).status_code==200
    extracted=client.post('/api/items/'+c['uuid']+'/extract',json={'kind':'experiences','content':'复用方法'}).json()
    assert extracted['item_type']=='experiences'
    assert client.get('/api/items/'+k['uuid']+'/recommendations').status_code==200
    inbox=client.post('/api/inbox',json={'content':'一句话即可保存'}).json()
    r=client.post('/api/items/'+inbox['uuid']+'/convert',json={'kind':'knowledge'})
    assert r.status_code==200 and r.json()['uuid']==inbox['uuid']

def test_validation_rollback(client,create):
    count=client.get('/api/entries').json()['total']
    assert client.post('/api/entries',json={'title':'错误关联','projects':['00000000-0000-0000-0000-000000000000']}).status_code==400
    assert client.get('/api/entries').json()['total']==count
    assert client.post('/api/knowledge',json={'title':'无效成熟度','details':{'maturity_level':9}}).status_code==422
    domain=create('domains',name='循环测试')
    assert client.put('/api/domains/'+domain['uuid'],json={'name':'循环','parent_id':domain['id']}).status_code==400

def test_sql_dump_rebuild(client,tmp_path):
    result=client.post('/api/export',json={'format':'sql'}).json()
    script=client.get(result['url']).text
    from contextlib import closing
    with closing(sqlite3.connect(tmp_path/'restored.db')) as db:
        db.executescript(script)
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
    with closing(sqlite3.connect(tmp_path/'restored.db')) as db:
        assert db.execute('SELECT count(*) FROM entries').fetchone()[0]>0
        assert db.execute("SELECT count(*) FROM entries_fts WHERE entries_fts MATCH '知识验证'").fetchone()[0]>=1

def test_ai_export_options_no_body_leak(client,create):
    k=create('knowledge',title='隐私开关测试',content='omit-body-unique',summary='omit-summary-unique',tags=['开关测试'],details={'source_title':'omit-detail-unique'})
    r=client.post('/api/export',json={'format':'context','filters':{'tag':'开关测试'},'options':{'include_content':False,'include_summary':False,'include_attachments':False}}).json()
    z=zipfile.ZipFile(io.BytesIO(client.get(r['url']).content))
    content=b''.join(z.read(n) for n in z.namelist())
    assert b'omit-body-unique' not in content
    assert b'omit-summary-unique' not in content
    assert b'omit-detail-unique' not in content

def test_asset_export_import_roundtrip(client,create):
    k=create('solutions',title='方案导入导出',content='步骤内容',tags=['资产迁移测试'],details={'steps':'真正步骤','confidence':'verified'})
    r=client.post('/api/export',json={'format':'jsonl','filters':{'tag':'资产迁移测试'}}).json()
    z=zipfile.ZipFile(io.BytesIO(client.get(r['url']).content))
    raw=z.read('entries.jsonl')
    p=client.post('/api/import/preview',files={'file':('assets.jsonl',raw)}).json()
    assert not p['errors'],p
    r=client.post('/api/import/confirm',json={'token':p['token'],'strategy':'overwrite'})
    assert r.status_code==200,r.text
    assert client.get('/api/solutions/'+k['uuid']).json()['details']['steps']=='真正步骤'
