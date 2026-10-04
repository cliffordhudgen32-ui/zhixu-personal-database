import csv
import io
import json
import secrets
import time
from copy import deepcopy
from datetime import date
from pathlib import Path
from uuid import UUID, uuid4
from pydantic import ValidationError
from sqlalchemy import select, or_
from fastapi import HTTPException
from app.config import HOME
from app.models import DailyEntry, Project, Person, Link, Attachment, AIConversation, now
from app.schemas import EntryInput, SCHEMAS
from app.services.records import save, serialize, hash_entry, audit

PREVIEWS = {}

def parse(filename, raw, mapping):
    if not isinstance(mapping, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in mapping.items()):
        raise HTTPException(400, '字段映射的名称须为文本')
    if len(set(mapping.values())) != len(mapping):
        raise HTTPException(400, '不同源字段不能映射为同一个目标字段')
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise HTTPException(400, '文件需要使用 UTF-8 编码')
    ext = Path(filename).suffix.lower()
    try:
        if ext == '.csv':
            csv.field_size_limit(2_000_000)
            rows = list(csv.DictReader(io.StringIO(text)))
            for row in rows:
                for key, value in row.items():
                    if isinstance(value, str) and value.startswith("'") and (value[1:].lstrip().startswith(('=', '+', '-', '@')) or value[1:2] == "'"):
                        row[key] = value[1:]
        elif ext == '.json':
            rows = json.loads(text)
            if isinstance(rows, dict):
                if rows.get('format') == 'pld-full':
                    raise HTTPException(400, '完整关系 JSON 用于开放迁移；本导入器接收记录数组。完整恢复请使用 ZIP 备份。')
                rows = rows.get('entries', [rows])
        elif ext == '.jsonl':
            rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        elif ext in ('.md', '.txt'):
            rows = [{'title': Path(filename).stem, 'content': text, 'date': date.today().isoformat()}]
        else:
            raise HTTPException(400, '支持 CSV、JSON、JSONL、Markdown 和 TXT')
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise ValueError()
        if len(rows) > 5000:
            raise HTTPException(400, '单次导入最多 5000 条，请拆分文件')
        mapped = [{mapping.get(k, k): v for k, v in row.items()} for row in rows]
        return mapped
    except (ValueError, TypeError):
        raise HTTPException(400, '文件结构无法解析，请检查格式')

def normalize(row):
    row = dict(row)
    for key in ('tags', 'projects', 'people', 'metadata_json', 'details', 'domain', 'topic', 'links', 'conversation'):
        value = row.get(key)
        if isinstance(value, str):
            if value.lstrip().startswith(('[', '{')):
                row[key] = json.loads(value)
            else:
                if key in ('tags', 'projects', 'people'):
                    row[key] = [x.strip() for x in value.split(',') if x.strip()]
                elif not value.strip():
                    row[key] = [] if key == 'links' else None if key in ('domain', 'topic') else {}
                else:
                    raise ValueError('结构化字段必须是 JSON')
    relation_data = {key: row.get(key, []) for key in ('projects', 'people')}
    relation_data['domain'] = row.get('domain')
    relation_data['topic'] = row.get('topic')
    relation_data['links'] = row.get('links') or []
    relation_data['conversation'] = row.get('conversation')
    relation_data['conversation_fields'] = set(relation_data['conversation'] or {}) if isinstance(relation_data['conversation'], dict) else set()
    row['tags'] = [x['name'] if isinstance(x, dict) else x for x in (row.get('tags') or [])]
    for key in ('projects', 'people'):
        row[key] = [x['uuid'] if isinstance(x, dict) else x for x in (row.get(key) or [])]
        relation_data[key] = relation_data[key] or []
    if 'item_type' not in row and row.get('type'):
        row['item_type'] = row['type']
    clean = {k: v for k, v in row.items() if k in EntryInput.model_fields and v is not None and not (v == '' and k in ('uuid', 'date', 'importance', 'privacy_level'))}
    clean.setdefault('origin','import')
    valid = EntryInput.model_validate(clean).model_dump(mode='json')
    from app.services.knowledge import DETAIL_SCHEMAS
    if valid['item_type'] in DETAIL_SCHEMAS:
        details = row.get('details') or {}
        if not isinstance(details, dict):
            raise ValueError('结构化详情须为对象')
        if row.get('id') and ((details.get('domain_id') and not relation_data['domain']) or
                (details.get('topic_id') and not relation_data['topic'])):
            raise ValueError('迁移领域与主题需要 UUID 定义')
        relation_data['file_uuid'] = details.get('file_uuid')
        # Internal attachment IDs belong to the source database. Binary files are
        # restored by backup; existing destination attachments can map by UUID.
        details = {k:v for k,v in details.items() if k in DETAIL_SCHEMAS[valid['item_type']].model_fields and k != 'file_id'}
        valid['details'] = DETAIL_SCHEMAS[valid['item_type']].model_validate(details).model_dump(mode='json')
        if valid['details'].get('confidence', 'unknown') not in ('unknown', 'low', 'medium', 'high', 'verified'):
            raise ValueError('可信度选项无效')
    elif row.get('details'):
        raise ValueError('该类型不支持结构化详情')
    if valid['item_type'] == 'conversations':
        conversation = relation_data['conversation'] or {'platform': '导入', 'conversation_title': valid['title'],
            'conversation_date': valid['date'], 'user_message': valid['content'], 'assistant_message': '', 'summary': valid['summary']}
        if not isinstance(conversation, dict):
            raise ValueError('对话须为对象')
        relation_data['conversation'] = SCHEMAS['conversations'].model_validate({k: v for k, v in conversation.items()
            if k in SCHEMAS['conversations'].model_fields and k not in ('uuid', 'project_id')}).model_dump(mode='json')
    valid['_original_content'] = row.get('original_content',valid['content'])
    valid['_supplied_fields'] = set(row)
    if not isinstance(valid['_original_content'], str) or len(valid['_original_content']) > 2_000_000:
        raise ValueError('原文无效')
    return valid, relation_data

def find_duplicate(session, data):
    if data.get('uuid'):
        obj = session.scalar(select(DailyEntry).where(DailyEntry.uuid == data['uuid']))
        if obj:
            return obj
    return session.scalar(select(DailyEntry).where(DailyEntry.content_hash == hash_entry(data)).order_by(DailyEntry.id))

def save_imported(session, data, relations, uid=None):
    from app.knowledge_models import ASSETS
    from app.services.knowledge import save_asset
    if data['item_type'] in ASSETS:
        return save_asset(session, data['item_type'], data, uid)
    if data['item_type'] == 'conversations':
        existing = session.scalar(select(AIConversation).where(AIConversation.uuid == uid)) if uid else None
        payload = dict(relations.get('conversation') or {})
        if existing:
            previous = serialize(existing, False)
            for key in SCHEMAS['conversations'].model_fields:
                if key not in relations.get('conversation_fields', set()) and key in previous:
                    payload[key] = previous[key]
        elif not payload:
            payload = {'platform': '导入', 'conversation_title': data['title'], 'conversation_date': data['date'], 'user_message': data['content']}
        payload.update(uuid=data.get('uuid'), privacy_level=data['privacy_level'], tags=data['tags'], summary=data['summary'])
        payload['project_id'] = session.scalar(select(Project.id).where(Project.uuid == data['projects'][0])) if data.get('projects') else None
        conversation = save(session, 'conversations', payload, uid, conversation_entry=data)
        return session.scalar(select(DailyEntry).where(DailyEntry.uuid == conversation.entry_uuid))
    return save(session, 'entries', data, uid)

def merge_overwrite(found, data, supplied):
    """A partial reading export must not erase fields the user excluded."""
    existing = serialize(found)
    for key in EntryInput.model_fields:
        if key not in supplied and key in existing:
            data[key] = existing[key]
    for key in ('projects', 'people'):
        if key not in supplied:
            data[key] = [item['uuid'] for item in existing[key]]
    if 'tags' not in supplied:
        data['tags'] = [item['name'] for item in existing['tags']]
    if 'details' not in supplied and existing.get('details') is not None:
        from app.services.knowledge import DETAIL_SCHEMAS
        data['details'] = {key: value for key, value in existing['details'].items() if key in DETAIL_SCHEMAS[found.item_type].model_fields}
    return data

def validate_taxonomy(session, kind, definition, seen=None):
    """Validate portable taxonomy definitions without trusting exported numeric IDs."""
    if not definition:
        return None
    from app.knowledge_models import Domain, Topic
    model = Domain if kind == 'domains' else Topic
    if isinstance(definition, str):
        uid = str(UUID(definition))
        if not session.scalar(select(model.id).where(model.uuid == uid)):
            raise ValueError('领域或主题 UUID 不存在')
        return {'uuid': uid}
    if not isinstance(definition, dict):
        raise ValueError('领域或主题须为对象或 UUID')
    uid = str(UUID(definition['uuid']))
    seen = set() if seen is None else set(seen)
    if uid in seen:
        raise ValueError('领域或主题形成循环')
    seen.add(uid)
    existing = session.scalar(select(model).where(model.uuid == uid))
    payload = {k: v for k, v in definition.items() if k in SCHEMAS[kind].model_fields and k not in ('parent_id', 'domain_id')}
    payload['uuid'] = uid
    if not existing:
        payload = SCHEMAS[kind].model_validate(payload).model_dump(mode='json')
    if definition.get('parent'):
        payload['parent'] = validate_taxonomy(session, kind, definition['parent'], seen)
    elif definition.get('parent_uuid'):
        payload['parent'] = validate_taxonomy(session, kind, definition['parent_uuid'], seen)
    if kind == 'topics':
        domain = definition.get('domain') or definition.get('domain_uuid')
        if domain:
            payload['domain'] = validate_taxonomy(session, 'domains', domain)
    return payload

def import_taxonomy(session, kind, definition, fallback_domain=None):
    if not definition:
        return None
    from app.knowledge_models import Domain, Topic
    model = Domain if kind == 'domains' else Topic
    existing = session.scalar(select(model).where(model.uuid == definition['uuid']))
    if existing:
        return existing
    payload = {k: v for k, v in definition.items() if k in SCHEMAS[kind].model_fields and k not in ('parent_id', 'domain_id')}
    parent = import_taxonomy(session, kind, definition.get('parent'), fallback_domain)
    payload['parent_id'] = parent.id if parent else None
    if kind == 'topics':
        domain = import_taxonomy(session, 'domains', definition.get('domain')) or fallback_domain
        payload['domain_id'] = domain.id if domain else None
    return save(session, kind, payload)

def preview(session, filename, raw, mapping):
    rows = parse(filename, raw, mapping)
    prepared, errors, seen = [], [], set()
    duplicate_count = 0
    for i, row in enumerate(rows):
        try:
            data, relations = normalize(row)
            h = hash_entry(data)
            found = find_duplicate(session, data)
            duplicate = bool(found or h in seen or (data.get('uuid') and data['uuid'] in seen))
            seen.update([h, data.get('uuid')])
            duplicate_count += int(duplicate)
            for key, model in [('projects', Project), ('people', Person)]:
                for j, v in enumerate(relations[key]):
                    uid = str(UUID(v['uuid'] if isinstance(v, dict) else v))
                    if not session.scalar(select(model).where(model.uuid == uid)) and not isinstance(v, dict):
                        raise ValueError('关联 UUID 不存在；请先创建项目/人物，或提供含 name/uuid 的对象')
                    if isinstance(v, dict):
                        relations[key][j] = SCHEMAS[key].model_validate({k: value for k, value in v.items() if k in SCHEMAS[key].model_fields}).model_dump(mode='json')
            relations['domain'] = validate_taxonomy(session, 'domains', relations['domain'])
            relations['topic'] = validate_taxonomy(session, 'topics', relations['topic'])
            from urllib.parse import urlparse
            for link in relations['links']:
                if not isinstance(link, dict) or not isinstance(link.get('url'), str):
                    raise ValueError('链接无效')
                url = urlparse(link['url'])
                if url.scheme not in ('http', 'https') or not url.netloc:
                    raise ValueError('链接必须是 HTTP/HTTPS')
                if link.get('uuid'):
                    link['uuid'] = str(UUID(link['uuid']))
                for field, limit in [('url', 2_000_000), ('title', 500), ('description', 2_000_000)]:
                    if field in link and (not isinstance(link[field], str) or len(link[field]) > limit):
                        raise ValueError('链接字段无效')
                if 'metadata_json' in link and not isinstance(link['metadata_json'], dict):
                    raise ValueError('链接扩展字段须为对象')
            prepared.append({'data': data, 'relations': relations, 'duplicate': duplicate,
                             'match_uuid': found.uuid if found else None, 'match_updated': found.updated_at if found else None})
        except (ValueError, KeyError, TypeError, AttributeError, ValidationError, RecursionError):
            errors.append({'line': i + 1, 'message': '字段无效，请检查标题、日期、UUID、关联关系和字段映射'})
    token = secrets.token_urlsafe(32)
    for key in list(PREVIEWS):
        if time.time() - PREVIEWS[key]['time'] > 1800:
            del PREVIEWS[key]
    if len(PREVIEWS) >= 20:
        PREVIEWS.pop(next(iter(PREVIEWS)))
    PREVIEWS[token] = {'time': time.time(), 'rows': prepared, 'errors': errors}
    return {'token': token, 'count': len(rows), 'valid_count': len(prepared), 'duplicates': duplicate_count,
            'errors': errors, 'fields': list(rows[0]) if rows else [],
            'preview': [{'title': x['data']['title'], 'date': x['data']['date'], 'duplicate': x['duplicate']} for x in prepared[:50]]}

def confirm(session, token, strategy):
    item = PREVIEWS.get(token)
    if not item or time.time() - item['time'] > 1800:
        raise HTTPException(400, '预览已过期，请重新上传预览')
    if item['errors']:
        raise HTTPException(400, '存在无效记录，请修正后重新预览')
    if strategy not in ('skip', 'overwrite', 'new'):
        raise HTTPException(400, '重复处理策略无效')
    imported = skipped = 0
    aliases = {}
    # Check every overwrite target before writing any row, then create cases before
    # experiences so UUID references work even when a file lists the source last.
    for row in item['rows']:
        found = find_duplicate(session, row['data'])
        if strategy == 'overwrite' and ((found.uuid if found else None) != row['match_uuid'] or
                (found and found.updated_at != row['match_updated'])):
            raise HTTPException(409, '预览后原记录已改变，请重新预览以免覆盖新修改')
        uid = row['data'].get('uuid')
        if uid:
            aliases.setdefault(uid, str(uuid4()) if strategy == 'new' else found.uuid if found else uid)
    used_new_uuids = set()
    for row in sorted(item['rows'], key=lambda r: r['data']['item_type'] != 'cases'):
        data = deepcopy(row['data'])
        original_content = data.pop('_original_content', data.get('content',''))
        supplied = data.pop('_supplied_fields')
        found = find_duplicate(session, data)
        if found and strategy == 'skip':
            skipped += 1
            continue
        if found and strategy == 'overwrite' and found.deleted_at:
            raise HTTPException(409, '重复记录位于回收站，请先恢复记录或作为新记录导入')
        if found and strategy == 'overwrite':
            data = merge_overwrite(found, data, supplied)
        for key, kind, model in [('projects', 'projects', Project), ('people', 'people', Person)]:
            for related in row['relations'][key]:
                if isinstance(related, dict) and not session.scalar(select(model).where(model.uuid == related['uuid'])):
                    from app.schemas import SCHEMAS
                    save(session, kind, {k: v for k, v in related.items() if k in SCHEMAS[kind].model_fields})
        if 'details' in data:
            domain = import_taxonomy(session, 'domains', row['relations'].get('domain'))
            topic = import_taxonomy(session, 'topics', row['relations'].get('topic'), domain)
            if domain:
                data['details']['domain_id'] = domain.id
            if topic:
                data['details']['topic_id'] = topic.id
            if data['details'].get('source_case_uuid'):
                original_case = data['details']['source_case_uuid']
                data['details']['source_case_uuid'] = aliases.get(original_case, original_case)
            if row['relations'].get('file_uuid'):
                attachment = session.scalar(select(Attachment).where(Attachment.uuid == row['relations']['file_uuid']))
                data['details']['file_id'] = attachment.id if attachment else None
        if found and strategy == 'overwrite':
            data['uuid'] = found.uuid
            if found.item_type != data.get('item_type','entries'):
                raise HTTPException(409,'重复内容的类型不同，不能覆盖；请跳过或作为新记录导入')
            obj = save_imported(session, data, row['relations'], found.uuid)
        else:
            if strategy == 'new':
                proposed = aliases.get(data.get('uuid'))
                data['uuid'] = proposed if proposed and proposed not in used_new_uuids else str(uuid4())
                used_new_uuids.add(data['uuid'])
            obj = save_imported(session, data, row['relations'])
            obj.original_content = original_content
        for related in row['relations']['links']:
            link_uid = related.get('uuid')
            existing = session.scalar(select(Link).where(Link.uuid == link_uid)) if link_uid and strategy != 'new' else None
            if existing:
                if existing.entry_id != obj.id:
                    raise HTTPException(409, '链接 UUID 已属于其他记录，不能移植关联')
                continue
            payload = {key: related[key] for key in ('url', 'title', 'description', 'metadata_json') if key in related}
            if link_uid and strategy != 'new':
                payload['uuid'] = link_uid
            session.add(Link(entry_id=obj.id, **payload))
        session.flush()
        imported += 1
    audit(session, '导入', 'entries', description=f'写入 {imported} 条，跳过 {skipped} 条')
    return {'imported': imported, 'skipped': skipped}
