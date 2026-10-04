import hashlib
from datetime import date
from uuid import uuid4
from sqlalchemy import select, func, or_, inspect, text
from sqlalchemy.orm import selectinload
from fastapi import HTTPException
from app.models import *
from app.schemas import SCHEMAS

def audit(session, action, entity, uid='', description=''):
    session.add(AuditLog(action=action, entity=entity, object_uuid=str(uid), description=description))

_UNLOADED_DETAILS = object()

def serialize(obj, relations=True, details=_UNLOADED_DETAILS):
    result = {}
    for col in inspect(obj.__class__).columns:
        value = getattr(obj, col.name)
        result[col.name] = value.isoformat() if isinstance(value, date) else value
    if isinstance(obj, DailyEntry) and relations:
        for key in ('tags', 'projects', 'people', 'attachments', 'links'):
            result[key] = [serialize(item, False) for item in getattr(obj, key)]
        from app.knowledge_models import ASSETS
        from sqlalchemy.orm import object_session
        session = object_session(obj)
        if obj.item_type in ASSETS:
            if details is not _UNLOADED_DETAILS:
                result['details'] = details or {}
            elif session:
                detail = session.scalar(select(ASSETS[obj.item_type]).where(ASSETS[obj.item_type].uuid == obj.uuid))
                result['details'] = serialize(detail, False) if detail else {}
        elif obj.item_type == 'conversations':
            if details is not _UNLOADED_DETAILS:
                result['conversation'] = details or {}
            elif session:
                conversation = session.scalar(select(AIConversation).where(AIConversation.entry_uuid == obj.uuid))
                result['conversation'] = serialize(conversation, False) if conversation else {}
    elif isinstance(obj, AIConversation) and relations and obj.entry_uuid:
        from sqlalchemy.orm import object_session
        session = object_session(obj)
        entry = session.scalar(select(DailyEntry).where(DailyEntry.uuid == obj.entry_uuid)) if session else None
        if entry:
            result.update(item_type='conversations', is_favorite=entry.is_favorite, is_archived=entry.is_archived,
                          original_content=entry.original_content,
                          attachments=[serialize(v, False) for v in entry.attachments],
                          links=[serialize(v, False) for v in entry.links],
                          people=[serialize(v, False) for v in entry.people],
                          projects=[serialize(v, False) for v in entry.projects])
    return result

def entry_details(session, entries):
    """Load typed fields once per asset type for a bounded entry batch."""
    from app.knowledge_models import ASSETS
    grouped = {}
    for entry in entries:
        if entry.item_type in ASSETS or entry.item_type == 'conversations':
            grouped.setdefault(entry.item_type, []).append(entry.uuid)
    result = {}
    for kind, identities in grouped.items():
        model = AIConversation if kind == 'conversations' else ASSETS[kind]
        result.update({row.uuid: serialize(row, False) for row in session.scalars(
            select(model).where(model.uuid.in_(identities)))})
    return result

def serialize_entries(session, entries):
    entries = list(entries)
    details = entry_details(session, entries)
    return [serialize(entry, details=details.get(entry.uuid)) for entry in entries]

def get(session, model, uid):
    obj = session.scalar(select(model).where(model.uuid == str(uid)))
    if obj is None:
        raise HTTPException(404, '没有找到这项数据')
    return obj

def hash_entry(data):
    raw = '\n'.join(str(data.get(k, '')) for k in ('date', 'title', 'content'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()

def save(session, kind, payload, uid=None, *, allow_asset=False, conversation_entry=None):
    validated = SCHEMAS[kind].model_validate(payload).model_dump(mode='python')
    if validated.get('uuid'):
        validated['uuid'] = str(validated['uuid'])
    else:
        validated.pop('uuid', None)
    model = ENTITIES[kind]
    obj = get(session, model, uid) if uid else model()
    if not uid and validated.get('uuid') and session.scalar(select(model.id).where(model.uuid == validated['uuid'])):
        raise HTTPException(409, '该 UUID 已存在，请编辑原数据或使用新的 UUID')
    old_version = serialize(obj) if uid and kind == 'entries' and obj.item_type not in __import__('app.knowledge_models',fromlist=['ASSETS']).ASSETS else None
    if uid and 'uuid' in validated and validated['uuid'] != uid:
        raise HTTPException(400, '不能修改永久 UUID')
    if isinstance(obj, DailyEntry):
        from app.knowledge_models import ASSETS
        item_type = validated.get('item_type', 'entries')
        if item_type not in ('entries', 'inbox', 'conversations', *ASSETS):
            raise HTTPException(400, '内容类型无效')
        if (item_type in ASSETS or item_type == 'conversations') and not allow_asset:
            raise HTTPException(400, '请通过对应知识资产接口保存结构化内容')
        if not uid:
            validated['original_content'] = validated.get('content', '')
        elif obj.item_type != validated.get('item_type', obj.item_type):
            raise HTTPException(400, '请通过收集箱整理功能转换内容类型')
        for field, target in [('projects', Project), ('people', Person)]:
            ids = set(str(v) for v in validated.pop(field))
            items = list(session.scalars(select(target).where(target.uuid.in_(ids))))
            if len(items) != len(ids):
                raise HTTPException(400, '关联的项目或人物不存在')
            setattr(obj, field, items)
        tags = []
        for name in dict.fromkeys(v.strip() for v in validated.pop('tags') if v.strip()):
            if len(name) > 100:
                raise HTTPException(400, '标签名称最长 100 字')
            tag = session.scalar(select(Tag).where(Tag.name == name))
            if tag is None:
                tag = Tag(name=name)
                session.add(tag)
                session.flush()
            tags.append(tag)
        obj.tags = tags
        validated['content_hash'] = hash_entry(validated)
    # Validate every actual model foreign key, including review UUIDs and topic domains.
    # SQLite constraint errors must not be the first user-facing validation step.
    for column in model.__table__.columns:
        value = validated.get(column.name)
        if value is None:
            continue
        for foreign_key in column.foreign_keys:
            target = foreign_key.column
            if not session.scalar(select(target).where(target == value)):
                raise HTTPException(400, '关联对象不存在')
    if kind == 'relations' and validated['from_uuid'] == validated['to_uuid']:
        raise HTTPException(400, '请关联另一项内容')
    if kind in ('domains', 'topics'):
        parent = validated.get('parent_id')
        seen = {obj.id} if uid else set()
        while parent:
            if parent in seen:
                raise HTTPException(400, '父级关系不能形成循环')
            seen.add(parent)
            node = session.get(model, parent)
            if node is None:
                raise HTTPException(400, '父级不存在')
            parent = node.parent_id
    if kind == 'tasks':
        # Generated provenance is immutable across ordinary manual task edits.
        # A task title/status may change without losing its original source.
        provenance = (obj.metadata_json or {}).get('ai_followup') if uid else None
        if isinstance(provenance, dict):
            validated['metadata_json'] = {**validated['metadata_json'], 'ai_followup': provenance}
            validated['origin'] = obj.origin
            validated['privacy_level'] = max(validated['privacy_level'], obj.privacy_level)
        validated['completed_at'] = (obj.completed_at or now()) if validated.get('status') == '已完成' else None
    if kind == 'conversations':
        from app.schemas import EntryInput
        identity = obj.uuid if uid else validated.get('uuid') or str(uuid4())
        envelope = session.scalar(select(DailyEntry).where(DailyEntry.uuid == identity))
        if envelope and envelope.item_type != 'conversations':
            raise HTTPException(409, '该 UUID 已属于其他内容，不能创建对话')
        common = {key: value for key, value in serialize(envelope).items() if key in EntryInput.model_fields} if envelope else {}
        if envelope:
            common['people'] = [person.uuid for person in envelope.people]
        common.update(title=validated['conversation_title'], date=validated['conversation_date'],
                      content='用户：\n' + validated['user_message'] + '\n\nAI：\n' + validated['assistant_message'],
                      summary=validated['summary'], tags=validated['tags'], privacy_level=validated['privacy_level'],
                      projects=[session.get(Project, validated['project_id']).uuid] if validated.get('project_id') else [],
                      source=validated['source_file'], ai_provider=validated['platform'],
                      origin=validated.get('origin', 'conversation'), metadata_json=validated['metadata_json'])
        if conversation_entry:
            common.update({key: value for key, value in conversation_entry.items() if key in EntryInput.model_fields})
        common.update(uuid=identity, item_type='conversations')
        envelope = save(session, 'entries', common, envelope.uuid if envelope else None, allow_asset=True)
        validated.update(uuid=identity, entry_uuid=envelope.uuid)
    for key, value in validated.items():
        setattr(obj, key, value)
    session.add(obj)
    session.flush()
    if isinstance(obj, DailyEntry):
        _protect_derived_privacy(session, obj)
    elif isinstance(obj, Task):
        _protect_task_privacy(session, obj)
    elif isinstance(obj, Project):
        for task in session.scalars(select(Task).where(Task.project_id == obj.id)):
            if isinstance((task.metadata_json or {}).get('ai_followup'), dict):
                _protect_task_privacy(session, task)
        session.flush()
    if old_version:
        from app.knowledge_models import Revision
        session.add(Revision(item_uuid=obj.uuid, reason='编辑记录', old_version=old_version, new_version=serialize(obj)))
    audit(session, '修改' if uid else '创建', kind, obj.uuid)
    return obj

def _protect_task_privacy(session, task):
    provenance = (task.metadata_json or {}).get('ai_followup')
    if not isinstance(provenance, dict):
        return
    identities = [str(value) for value in provenance.get('source_uuids', []) if value]
    if provenance.get('archived_uuid'):
        identities.append(str(provenance['archived_uuid']))
    source_level = session.scalar(select(func.max(DailyEntry.privacy_level)).where(DailyEntry.uuid.in_(identities))) if identities else 1
    project = session.get(Project, task.project_id) if task.project_id else None
    task.privacy_level = max(task.privacy_level, source_level or 1, project.privacy_level if project else 1)
    session.flush()

def _protect_derived_privacy(session, entry):
    """Source privacy also protects copied AI archives and confirmed follow-ups."""
    from app.workflow_models import AIArchive
    from app.knowledge_models import KnowledgeRelation
    archive = session.scalar(select(AIArchive).where(AIArchive.entry_uuid == entry.uuid))
    if archive:
        source_ids = (entry.metadata_json or {}).get('source_uuids', [])
        if source_ids:
            source_level = session.scalar(select(func.max(DailyEntry.privacy_level)).where(DailyEntry.uuid.in_(source_ids)))
            entry.privacy_level = max(entry.privacy_level, source_level or 1)
    # The relation's reverse index keeps this bounded to actual descendants.
    seen, pending = set(), [entry]
    while pending:
        source = pending.pop()
        if source.uuid in seen:
            continue
        seen.add(source.uuid)
        followups = session.scalars(select(Task).where(Task.privacy_level < source.privacy_level).where(
            or_(Task.metadata_json['ai_followup']['archived_uuid'].as_string() == source.uuid,
                text("EXISTS (SELECT 1 FROM json_each(tasks.metadata_json, '$.ai_followup.source_uuids') AS followup_source WHERE followup_source.value = :followup_source_uuid)"))
            .params(followup_source_uuid=source.uuid)))
        for task in followups:
            task.privacy_level = source.privacy_level
        derived = list(session.scalars(select(DailyEntry).join(AIArchive, AIArchive.entry_uuid == DailyEntry.uuid)
            .join(KnowledgeRelation, KnowledgeRelation.from_uuid == DailyEntry.uuid)
            .where(KnowledgeRelation.to_uuid == source.uuid, KnowledgeRelation.relation_type == 'derived_from')))
        for dependent in derived:
            if dependent.privacy_level < source.privacy_level:
                dependent.privacy_level = source.privacy_level
                pending.append(dependent)
    session.flush()

def query_entries(filters, include_sensitive=True):
    from app.services.search import keyword_condition
    q = select(DailyEntry)
    q = q.where(DailyEntry.deleted_at.is_not(None) if str(filters.get('trash', '')).lower() in ('true', '1') else DailyEntry.deleted_at.is_(None))
    for field in ('category', 'status', 'importance', 'privacy_level'):
        if filters.get(field) not in (None, ''):
            q = q.where(getattr(DailyEntry, field) == filters[field])
    if filters.get('item_type'):
        q = q.where(DailyEntry.item_type == filters['item_type'])
    from app.knowledge_models import ASSETS
    kinds = [filters['item_type']] if filters.get('item_type') in ASSETS else list(ASSETS)
    for field in ('domain_id','topic_id','maturity_level','knowledge_type','case_type','source_type','outcome','is_reviewed','confidence','verification_status'):
        if filters.get(field) not in (None, ''):
            clauses = []
            for kind in kinds:
                model = ASSETS[kind]
                if hasattr(model,field):
                    value = filters[field]
                    if field == 'is_reviewed':
                        value = str(value).lower() in ('true','1')
                    clauses.append(DailyEntry.uuid.in_(select(model.uuid).where(getattr(model,field)==value)))
            q = q.where(or_(*clauses) if clauses else False)
    if filters.get('reusable') == 'true':
        from app.knowledge_models import Case
        q = q.where(DailyEntry.uuid.in_(select(Case.uuid).where(Case.reusable_method != '')))
    if filters.get('min_used'):
        try:
            minimum = int(filters['min_used'])
            if minimum < 0:
                raise ValueError()
        except (TypeError, ValueError):
            raise HTTPException(400, '复用次数应为非负整数')
        q = q.where(or_(*(DailyEntry.uuid.in_(select(ASSETS[k].uuid).where(ASSETS[k].times_used >= minimum)) for k in kinds)))
    for field in ('is_favorite', 'is_archived'):
        if filters.get(field) not in (None, ''):
            q = q.where(getattr(DailyEntry, field) == (str(filters[field]).lower() in ('true', '1')))
    for param, field, op in [('start', 'date', 'ge'), ('end', 'date', 'le')]:
        if filters.get(param):
            try:
                value = date.fromisoformat(str(filters[param]))
            except ValueError:
                raise HTTPException(400, '日期格式应为 YYYY-MM-DD')
            q = q.where(getattr(DailyEntry, field) >= value if op == 'ge' else getattr(DailyEntry, field) <= value)
    for field, model, attr in [('project', Project, 'projects'), ('person', Person, 'people'), ('tag', Tag, 'tags')]:
        if filters.get(field):
            value = str(filters[field])
            q = q.where(getattr(DailyEntry, attr).any(or_(model.uuid == value, model.name == value)))
    if filters.get('q'):
        q = q.where(keyword_condition(str(filters['q']), str(filters.get('scope', 'all'))))
    if not include_sensitive:
        q = q.where(DailyEntry.privacy_level < 3)
    return q

def listing(session, filters, page=1, size=30):
    size = max(1, min(100, size))
    page = max(1, page)
    query = query_entries(filters)
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    sort = DailyEntry.updated_at if filters.get('sort') == 'updated' else DailyEntry.date
    items = session.scalars(query.order_by(sort.desc(), DailyEntry.id.desc()).offset((page - 1) * size).limit(size))
    return {'items': serialize_entries(session, items), 'total': total, 'page': page, 'size': size}
