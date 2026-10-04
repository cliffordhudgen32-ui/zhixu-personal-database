from datetime import date
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select, func, or_, String, Text
from app.database import transaction
from app.models import DailyEntry, ENTITIES, Review, Task, Event, now
from app.knowledge_models import *
from app.schemas import AssetInput
from app.services.records import serialize, get, listing, save, audit
from app.services.knowledge import *

router = APIRouter()

@router.get('/api/global-search')
def global_search(q: str = '', page: int = 1, size: int = 20):
    result = {}
    with transaction() as s:
        for kind in ['entries','inbox','conversations',*ASSETS]:
            result[kind] = listing(s,{'q':q,'item_type':kind},page,size)
        for kind in ['projects','people','tasks','events']:
            model = ENTITIES[kind]
            cols = [c for c in model.__table__.columns if isinstance(c.type,(String,Text)) and c.name not in ('uuid','created_at','updated_at')]
            query = select(model)
            for term in q.split()[:20]:
                query = query.where(or_(*(c.contains(term,autoescape=True) for c in cols)))
            count = s.scalar(select(func.count()).select_from(query.subquery()))
            result[kind] = {'total':count,'items':[serialize(v) for v in s.scalars(query.limit(min(max(size,1),100)).offset((max(page,1)-1)*min(max(size,1),100)))]}
    return result

@router.post('/api/inbox')
def inbox(payload: dict):
    content = str(payload.get('content','')).strip()
    if not content:
        raise HTTPException(400,'请输入要收集的内容')
    with transaction() as s:
        return serialize(save(s,'entries',{'title':content.splitlines()[0][:100],'content':content,'item_type':'inbox','privacy_level':payload.get('privacy_level',1)}))

@router.post('/api/items/{uid}/convert')
def convert(uid: str,payload: dict):
    with transaction() as s:
        return convert_inbox(s,uid,payload.get('kind','entries'))

@router.post('/api/items/{uid}/reuse')
def use_again(uid: str,payload: dict):
    with transaction() as s:
        return reuse(s,uid,payload)

@router.get('/api/items/{uid}/history')
def history(uid: str, page: int = 1):
    with transaction() as s:
        get(s,DailyEntry,uid)
        return {'revisions':[serialize(v) for v in s.scalars(select(Revision).where(Revision.item_uuid==uid).order_by(Revision.id.desc()).limit(20).offset((max(page,1)-1)*20))],
                'reuse':[serialize(v) for v in s.scalars(select(ReuseLog).where(ReuseLog.item_uuid==uid).order_by(ReuseLog.id.desc()).limit(20).offset((max(page,1)-1)*20))]}

@router.get('/api/items/{uid}/relations')
def relations(uid: str, page: int = 1):
    with transaction() as s:
        result = []
        for relation in s.scalars(select(KnowledgeRelation).where(or_(KnowledgeRelation.from_uuid==uid,KnowledgeRelation.to_uuid==uid)).limit(50).offset((max(page,1)-1)*50)):
            data = serialize(relation)
            target = get(s,DailyEntry,relation.to_uuid if relation.from_uuid==uid else relation.from_uuid)
            data['target'] = {'uuid':target.uuid,'title':target.title,'item_type':target.item_type}
            result.append(data)
        return result

@router.post('/api/item-relations')
def create_relation(payload: dict):
    with transaction() as s:
        return serialize(add_relation(s,payload))

@router.get('/api/items/{uid}/recommendations')
def recommend(uid: str):
    with transaction() as s:
        return {'similar_cases':find_similar_cases(s,uid),'reusable':recommend_reusable_knowledge(s,uid)}

@router.post('/api/knowledge/{uid}/reviewed')
def reviewed(uid: str,payload: dict):
    with transaction() as s:
        entry = get(s,DailyEntry,uid)
        detail = get(s,Knowledge,uid)
        detail.review_count += 1; detail.last_reviewed_at=now()
        try:
            detail.next_review_date = date.fromisoformat(payload['next_review_date']) if payload.get('next_review_date') else None
        except ValueError:
            raise HTTPException(400,'下次复习日期无效')
        audit(s,'复习','knowledge',uid)
        return serialize(detail)

@router.post('/api/items/{uid}/extract')
def extract(uid: str,payload: dict):
    kind = payload.get('kind','experiences')
    if kind not in ('knowledge','solutions','experiences'):
        raise HTTPException(400,'请选择知识、方案或经验')
    with transaction() as s:
        original = get(s,DailyEntry,uid)
        title = str(payload.get('title','从「' + original.title + '」提炼'))[:500]
        content = str(payload.get('content',''))
        item = save_asset(s,kind,{'title':title,'content':content,'privacy_level':original.privacy_level,
                               'content_nature':'experience','tags':[t.name for t in original.tags],
                               'projects':[p.uuid for p in original.projects], 'people':[p.uuid for p in original.people]})
        add_relation(s,{'from_uuid':item.uuid,'to_uuid':uid,'relation_type':'derived_from','description':'用户手动提炼，保留原始资料'})
        return serialize(item)

@router.post('/api/cases/{uid}/review')
def case_review(uid: str,payload: dict):
    with transaction() as s:
        case = get(s,Case,uid)
        data = dict(payload) | {'date':date.today().isoformat(),'review_type':'case','target_type':'cases','target_uuid':uid}
        review = save(s,'reviews',data)
        case.is_reviewed=True
        return serialize(review)

@router.post('/api/cases/{uid}/associate')
def case_associate(uid: str,payload: dict):
    with transaction() as s:
        get(s,Case,uid)
        kind = payload.get('kind')
        target = payload.get('uuid')
        if kind == 'tasks':
            get(s,Task,target); model=CaseTask; key='task_uuid'
        elif kind == 'events':
            get(s,Event,target); model=CaseEvent; key='event_uuid'
        else:
            raise HTTPException(400,'支持关联任务或事件')
        if not s.get(model,(uid,target)):
            s.add(model(**{'case_uuid':uid,key:target}))
        return {'ok':True}

@router.get('/api/cases/{uid}/associations')
def case_associations(uid: str):
    with transaction() as s:
        return {'tasks':[serialize(x) for x in s.scalars(select(Task).join(CaseTask,CaseTask.task_uuid==Task.uuid).where(CaseTask.case_uuid==uid).limit(100))],
                'events':[serialize(x) for x in s.scalars(select(Event).join(CaseEvent,CaseEvent.event_uuid==Event.uuid).where(CaseEvent.case_uuid==uid).limit(100))]}

def register(kind):
    def create(payload: AssetInput):
        with transaction() as s:
            return serialize(save_asset(s,kind,payload.model_dump(mode='json')))
    def update(uid: str,payload: AssetInput):
        with transaction() as s:
            return serialize(save_asset(s,kind,payload.model_dump(mode='json'),uid))
    def list_assets(request: Request,page: int=1,size: int=30):
        with transaction() as s:
            return listing(s,dict(request.query_params)|{'item_type':kind},page,size)
    def read(uid: str):
        with transaction() as s:
            entry=get(s,DailyEntry,uid)
            if entry.item_type != kind:
                raise HTTPException(404,'类型不匹配')
            return serialize(entry)
    def remove(uid: str):
        with transaction() as s:
            entry=get(s,DailyEntry,uid)
            if entry.item_type != kind:
                raise HTTPException(404,'类型不匹配')
            entry.deleted_at=now(); audit(s,'删除',kind,uid)
        return {'ok':True}
    router.post('/api/'+kind,name='create_'+kind)(create)
    router.put('/api/'+kind+'/{uid}',name='update_'+kind)(update)
    router.get('/api/'+kind,name='list_'+kind)(list_assets)
    router.get('/api/'+kind+'/{uid}',name='read_'+kind)(read)
    router.delete('/api/'+kind+'/{uid}',name='delete_'+kind)(remove)

for kind in ASSETS:
    register(kind)
