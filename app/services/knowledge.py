from datetime import date
import re
from sqlalchemy import select, or_, func, String, Text, Integer, Boolean, Date
from fastapi import HTTPException
from pydantic import create_model, Field
from app.models import DailyEntry, Project, Person, Task, Event, Review, Attachment, now
from app.knowledge_models import *
from app.schemas import AssetInput, InputBase
from app.services.records import save, serialize, serialize_entries, get, audit

DETAIL_SCHEMAS = {}
READONLY = {'id','uuid','times_used','last_used_at','reuse_score','review_count','last_reviewed_at','is_reviewed'}
for name, model in ASSETS.items():
    fields = {}
    for c in model.__table__.columns:
        if c.name in READONLY:
            continue
        typ = date if isinstance(c.type,Date) else bool if isinstance(c.type,Boolean) else int if isinstance(c.type,Integer) else str
        default = None if c.nullable else c.default.arg if c.default is not None and c.default.is_scalar else ''
        if c.nullable:
            typ = typ | None
        if c.name == 'maturity_level':
            default = Field(default=1, ge=1, le=6)
        if c.name == 'progress':
            default = Field(default=0, ge=0, le=100)
        if c.name in ('times_verified','recurrence_count'):
            default = Field(default=0, ge=0)
        if c.name == 'difficulty':
            default = Field(default=1, ge=1, le=5)
        fields[c.name] = (typ, default)
    DETAIL_SCHEMAS[name] = create_model(name + 'Details', __base__=InputBase, **fields)

def validate_details(session, kind, details):
    data = DETAIL_SCHEMAS[kind].model_validate(details).model_dump()
    if 'confidence' in data and data['confidence'] not in ('unknown','low','medium','high','verified'):
        raise HTTPException(400,'可信度选项无效')
    for field, model in [('domain_id',Domain),('topic_id',Topic)]:
        if data.get(field) and not session.get(model,data[field]):
            raise HTTPException(400,'领域或主题不存在')
    if data.get('topic_id') and data.get('domain_id'):
        topic = session.get(Topic,data['topic_id'])
        if topic.domain_id and topic.domain_id != data['domain_id']:
            raise HTTPException(400,'主题不属于所选领域')
    if data.get('topic_id') and not data.get('domain_id'):
        data['domain_id'] = session.get(Topic, data['topic_id']).domain_id
    if data.get('source_case_uuid'):
        case = session.scalar(select(Case).where(Case.uuid == data['source_case_uuid']))
        source_entry = session.scalar(select(DailyEntry).where(DailyEntry.uuid == data['source_case_uuid']))
        if not case or not source_entry or source_entry.deleted_at:
            raise HTTPException(400, '来源案例不存在或已进入回收站')
    if data.get('file_id'):
        if not session.get(Attachment, data['file_id']):
            raise HTTPException(400, '来源附件不存在')
    return data

def save_asset(session, kind, payload, uid=None):
    if kind not in ASSETS:
        raise HTTPException(404,'未知知识资产类型')
    p = AssetInput.model_validate(payload).model_dump(mode='json')
    raw = p.pop('details'); reason = p.pop('revision_reason')
    data = validate_details(session,kind,raw)
    old = serialize(get(session,DailyEntry,uid)) if uid else None
    if old and old['item_type'] != kind:
        raise HTTPException(400,'资产类型不匹配')
    p['item_type'] = kind
    entry = save(session,'entries',p,uid,allow_asset=True)
    detail = session.scalar(select(ASSETS[kind]).where(ASSETS[kind].uuid == entry.uuid)) if uid else ASSETS[kind](uuid=entry.uuid)
    if detail is None:
        raise HTTPException(409, '结构化资产数据缺失，请检查数据库完整性')
    for key,value in data.items():
        setattr(detail,key,value)
    session.add(detail); session.flush()
    # Preserve manually/AI-normalized prose. Only the managed derived suffix is
    # replaced when structured fields change; primary detail columns remain the
    # source of truth and the FTS projection is rebuildable.
    normalized = p.get('normalized_content', '')
    normalized = re.sub(r'\n\n<!-- pld-structured-fields:[0-9a-fA-F-]{36} -->\n.*?\n<!-- /pld-structured-fields -->\Z',
                        '', normalized, flags=re.DOTALL)
    if old:
        previous_fields = DETAIL_SCHEMAS[kind].model_fields
        previous_projection = '\n'.join(str(value) for key, value in old.get('details', {}).items()
                                        if key in previous_fields and isinstance(value, str) and value)
        if normalized == old.get('normalized_content') == previous_projection:
            normalized = ''  # Upgrade the legacy derived-only projection.
    projection = '\n'.join(str(value) for value in data.values() if isinstance(value, str) and value)
    entry.normalized_content = normalized + (f'\n\n<!-- pld-structured-fields:{entry.uuid} -->\n' + projection +
        '\n<!-- /pld-structured-fields -->' if projection else '')
    session.flush()
    if old:
        session.add(Revision(item_uuid=entry.uuid,reason=reason[:2000],old_version=old,new_version=serialize(entry)))
    return entry

def add_relation(session,payload):
    for key in ('from_uuid','to_uuid'):
        get(session,DailyEntry,payload[key])
    if payload['from_uuid'] == payload['to_uuid']:
        raise HTTPException(400,'请关联另一项内容')
    return save(session,'relations',payload)

def reuse(session,uid,payload):
    entry = get(session,DailyEntry,uid)
    if entry.item_type not in ('knowledge','cases','solutions','experiences'):
        raise HTTPException(400,'该类型不支持复用统计')
    project_id = payload.get('project_id') or None
    if project_id and not session.get(Project,project_id):
        raise HTTPException(400,'项目不存在')
    obj = session.scalar(select(ASSETS[entry.item_type]).where(ASSETS[entry.item_type].uuid == uid))
    if obj is None:
        raise HTTPException(409, '结构化资产数据缺失，请检查数据库完整性')
    if entry.deleted_at:
        raise HTTPException(400, '请先从回收站恢复内容再记录复用')
    obj.times_used += 1; obj.last_used_at = now(); obj.reuse_score = float(obj.times_used)
    row = ReuseLog(item_uuid=uid,project_id=project_id,used_at=now(),result=str(payload.get('result','')),notes=str(payload.get('notes','')))
    session.add(row); session.flush()
    audit(session,'复用','entries',uid)
    return serialize(obj)

def convert_inbox(session,uid,kind):
    entry = get(session,DailyEntry,uid)
    if entry.item_type != 'inbox':
        raise HTTPException(400,'仅收集箱内容可以直接整理类型')
    if kind in ASSETS or kind == 'entries':
        old = serialize(entry)
        entry.item_type = kind
        if kind in ASSETS:
            session.add(ASSETS[kind](uuid=uid))
        session.flush()
        session.add(Revision(item_uuid=uid,reason='收集箱整理为 ' + kind,old_version=old,new_version=serialize(entry)))
        audit(session,'整理收集箱','entries',uid)
        return serialize(entry)
    if kind in ('tasks','projects'):
        obj = save(session,kind, {'title' if kind == 'tasks' else 'name':entry.title,'description':entry.content})
        entry.is_archived = True
        entry.item_type = 'entries'
        entry.metadata_json = entry.metadata_json | {'converted_to':kind,'converted_uuid':obj.uuid}
        if kind == 'projects':
            entry.projects.append(obj)
        return serialize(obj)
    raise HTTPException(400,'不支持此整理类型')

def recommendations(session, uid, cases_only=False):
    from app.services.search import keyword_condition
    item = get(session,DailyEntry,uid)
    kinds = ['cases'] if cases_only else ['knowledge','cases','solutions','experiences']
    tag_ids = [t.id for t in item.tags]
    from app.models import Tag
    candidates = []
    if tag_ids:
        candidates.append(DailyEntry.tags.any(Tag.id.in_(tag_ids)))
    if item.item_type in ASSETS:
        detail = session.scalar(select(ASSETS[item.item_type]).where(ASSETS[item.item_type].uuid==uid))
        if detail and detail.domain_id:
            candidates.extend(DailyEntry.uuid.in_(select(ASSETS[k].uuid).where(ASSETS[k].domain_id==detail.domain_id)) for k in kinds)
    terms = item.title.split()[:5]
    candidates.extend(keyword_condition(t) for t in terms if t)
    rows = session.scalars(select(DailyEntry).where(DailyEntry.uuid != uid,DailyEntry.deleted_at.is_(None),DailyEntry.item_type.in_(kinds),or_(*candidates)).order_by(DailyEntry.importance.desc()).limit(12))
    return serialize_entries(session, rows)

def find_similar_cases(session,uid):
    return recommendations(session,uid,True)

def recommend_reusable_knowledge(session,uid):
    return recommendations(session,uid)
