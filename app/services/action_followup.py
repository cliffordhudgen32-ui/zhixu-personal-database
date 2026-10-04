"""Turn explicitly written follow-up plans into tasks only after a manual request.

This is a bounded Markdown section parser, not an AI extractor. No task date,
completion, project, or factual detail is inferred from the prose.
"""
import hashlib
import re
from uuid import UUID, uuid5

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import load_only, raiseload

from app.database import transaction
from app.models import DailyEntry, Project, Task
from app.services.records import audit, get, save, serialize
from app.workflow_models import AIDraft

NAMESPACE = UUID('f6ad0e10-3864-41bd-a19b-920c32914136')
MAX_CANDIDATES = 100
MAX_CANDIDATE_CHARS = 10000
PLAN_HEADINGS = frozenset(('后续计划', '后续事项', '后续行动', '后续安排', '下一步',
    '下一步计划', '下一步行动', '下一步安排', '待办事项', '明日计划', '行动计划', '需要跟进'))
NUMBER_PREFIX = re.compile(r'^(?:[一二三四五六七八九十百零〇\d]+[、.)．）]|[（(][一二三四五六七八九十百零〇\d]+[）)])\s*')
BULLET = re.compile(r'^\s*(?:[-+*]|\d+[.)．、])\s+')
CHECKBOX = re.compile(r'^\[([ xX✓✔])\]\s*')


def _heading_name(text):
    text = text.strip().strip('#').strip()
    text = text.strip('*_').strip()
    text = NUMBER_PREFIX.sub('', text).strip()
    return text.rstrip('：:').strip()


def _plain_heading(line):
    """Recognize an explicit label, including '下一步：原句', without NLP."""
    raw = line.strip().strip('*_').strip()
    for split in ('：', ':'):
        if split in raw:
            label, remainder = raw.split(split, 1)
            name = _heading_name(label)
            if name in PLAN_HEADINGS:
                return name, remainder.strip()
    name = _heading_name(raw)
    return (name, '') if name in PLAN_HEADINGS else None


def parse_candidates(content):
    """Return exact, deduplicated plan text and report every omitted long item."""
    candidates, seen, omitted = [], set(), 0
    section, section_level, lines, fenced, fence_marker, fence_size, skip_item = '', 0, [], False, '', 0, False

    def finish():
        nonlocal lines, omitted, skip_item
        text = '\n'.join(lines).strip()
        lines = []
        if skip_item:
            skip_item = False
            return
        if not text:
            return
        identity = hashlib.sha256(text.encode('utf-8')).hexdigest()
        if identity in seen:
            return
        seen.add(identity)
        if len(text) > MAX_CANDIDATE_CHARS or len(candidates) >= MAX_CANDIDATES:
            omitted += 1
            return
        title = ' '.join(text.split())
        candidates.append({'id': identity, 'section': section, 'source_text': text,
                           'suggested_title': title[:500], 'title_truncated': len(title) > 500})

    source_lines = str(content or '').splitlines()
    skip_underline = -1
    for index, line in enumerate(source_lines):
        if index == skip_underline:
            continue
        stripped = line.strip()
        fence = re.match(r'^(`{3,}|~{3,})', stripped)
        if fence:
            finish()
            if not fenced:
                fenced, fence_marker, fence_size = True, fence.group(1)[0], len(fence.group(1))
            elif fence.group(1)[0] == fence_marker and len(fence.group(1)) >= fence_size:
                fenced = False
            continue
        if fenced:
            continue
        match = re.match(r'^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$', line)
        underline = re.fullmatch(r' {0,3}(=+|-+)\s*', source_lines[index + 1]) if index + 1 < len(source_lines) else None
        setext = bool(underline and stripped and not stripped.startswith('>') and not BULLET.match(line))
        if match or setext:
            finish()
            if match:
                level, name = len(match.group(1)), _heading_name(match.group(2))
            else:
                level, name = (1 if underline.group(1).startswith('=') else 2), _heading_name(line)
                skip_underline = index + 1
            if name in PLAN_HEADINGS:
                section, section_level = name, level
            elif section and level <= section_level:
                section, section_level = '', 0
            continue
        plain = _plain_heading(line)
        if plain:
            finish()
            section, section_level = plain[0], 6
            if plain[1]:
                lines.append(plain[1])
            continue
        # Another explicit plain section label ends an unheaded plan block.
        if section and section_level == 6 and re.fullmatch(r'[^\s：:]{1,40}[：:]', stripped):
            finish()
            section, section_level = '', 0
            continue
        if not section:
            continue
        if not stripped:
            finish()
            continue
        if stripped.startswith('>') or re.fullmatch(r'[-*_]{3,}', stripped):
            finish()
            continue
        bullet = BULLET.match(line)
        if bullet:
            finish()
            text = line[bullet.end():].strip()
            checkbox = CHECKBOX.match(text)
            if checkbox:
                skip_item = checkbox.group(1) not in (' ', '')
                text = text[checkbox.end():].strip()
            lines.append(text)
        elif lines and (line[:1].isspace() or skip_item):
            lines.append(stripped)
        else:
            finish()
            lines.append(stripped)
    finish()
    return {'items': candidates, 'omitted': omitted}


def task_uuid(draft_uuid, candidate_id):
    return str(uuid5(NAMESPACE, str(draft_uuid) + ':' + candidate_id))


def _eligible(draft):
    if draft.status not in ('pending_review', 'approved'):
        raise HTTPException(409, '只有待审核或已确认归档的草稿可以提取后续事项')


def _source_options(session, draft):
    snapshots = {str(item.get('uuid')): item for item in draft.source_records or [] if item.get('uuid')}
    entries = list(session.scalars(select(DailyEntry).options(raiseload('*'), load_only(
        DailyEntry.uuid, DailyEntry.title, DailyEntry.date, DailyEntry.privacy_level,
        DailyEntry.generated_by_ai, DailyEntry.metadata_json, DailyEntry.deleted_at, DailyEntry.updated_at))
        .where(DailyEntry.uuid.in_(snapshots)))) if snapshots else []
    active = {entry.uuid: entry for entry in entries if not entry.deleted_at}
    originals = [entry for entry in active.values() if not entry.generated_by_ai and
                 not (entry.metadata_json or {}).get('ai_workflow_archive')]
    sources = [{'uuid': entry.uuid, 'title': entry.title, 'date': entry.date.isoformat(),
                'privacy_level': entry.privacy_level} for entry in originals]
    return snapshots, entries, active, sources


def candidate_options(uid):
    with transaction() as session:
        draft = get(session, AIDraft, uid)
        _eligible(draft)
        parsed = parse_candidates(draft.content)
        snapshots, entries, active, sources = _source_options(session, draft)
        projects = list(session.scalars(select(Project).order_by(Project.name, Project.id).limit(100)))
        identities = [task_uuid(draft.uuid, candidate['id']) for candidate in parsed['items']]
        tasks = {task.uuid: task for task in session.scalars(select(Task).where(Task.uuid.in_(identities)))}
        for candidate in parsed['items']:
            task = tasks.get(task_uuid(draft.uuid, candidate['id']))
            candidate['task'] = ({'uuid': task.uuid, 'title': task.title, 'status': task.status}
                                 if task and (task.metadata_json or {}).get('ai_followup', {}).get('draft_uuid') == draft.uuid else None)
        warnings = ['候选只来自明确的后续计划段落，仍需核对；待审核草稿中的内容尚未确认归档。'
                    if draft.status == 'pending_review' else '候选来自已归档草稿的原有计划，是否仍需执行由你确认。',
                    '系统不推断截止日期、关联项目或完成状态；点击创建后才保存为待办。']
        if len(active) != len(snapshots):
            warnings.append('部分来源已删除或进入回收站。已创建待办可查看，新的待办需先恢复来源或重新整理。')
        if any(entry.updated_at != snapshots[entry.uuid].get('updated_at') for entry in entries):
            warnings.append('部分原记录在生成草稿后已修改。请核对当前原记录与计划原句；创建待办不会替你确认事实。')
        if parsed['omitted']:
            warnings.append(f"有 {parsed['omitted']} 项因过长或超过 {MAX_CANDIDATES} 项上限未显示，请手动整理。")
        return {'draft_uuid': draft.uuid, 'revision': draft.revision, 'status': draft.status,
                'content_hash': hashlib.sha256(draft.content.encode('utf-8')).hexdigest(),
                'items': parsed['items'], 'omitted': parsed['omitted'], 'sources': sources,
                'projects': [{'uuid': project.uuid, 'name': project.name} for project in projects],
                'warnings': warnings}


def create_task(uid, candidate_id, payload):
    """Atomically validate the draft, then create one manual pending task."""
    if payload.confirmation != '创建待办':
        raise HTTPException(400, '需要明确确认创建待办')
    title = payload.title.strip()
    if not title:
        raise HTTPException(400, '请填写待办标题')
    with transaction() as session:
        draft = get(session, AIDraft, uid)
        identity = task_uuid(draft.uuid, candidate_id)
        existing = session.scalar(select(Task).where(Task.uuid == identity))
        # Successful response lost? Return the original task, without overwriting
        # later manual edits or completion, even if the draft has since changed.
        if existing:
            provenance = (existing.metadata_json or {}).get('ai_followup', {})
            if provenance.get('draft_uuid') != draft.uuid or provenance.get('candidate_id') != candidate_id:
                raise HTTPException(409, '该待办标识已被其他内容使用，请检查数据')
            return {'task': serialize(existing, False), 'created': False}
        _eligible(draft)
        if draft.revision != payload.revision or hashlib.sha256(draft.content.encode('utf-8')).hexdigest() != payload.content_hash:
            raise HTTPException(409, '草稿已变化，请刷新后续事项后再创建；已填写内容保留在本页')
        candidate = next((item for item in parse_candidates(draft.content)['items'] if item['id'] == candidate_id), None)
        if not candidate:
            raise HTTPException(409, '这条后续事项已变化或不在明确计划段落中，请重新选择')
        snapshots, entries, active, sources = _source_options(session, draft)
        if len(active) != len(snapshots):
            raise HTTPException(409, '草稿来源已删除或进入回收站，请先恢复来源或重新整理')
        source_uuid = str(payload.source_uuid) if payload.source_uuid else None
        if source_uuid and source_uuid not in {item['uuid'] for item in sources}:
            raise HTTPException(400, '只能关联本草稿仍有效的原始记录')
        project = get(session, Project, payload.project_uuid) if payload.project_uuid else None
        archive = session.scalar(select(DailyEntry).where(DailyEntry.uuid == draft.archived_uuid)) if draft.archived_uuid else None
        if draft.status == 'approved' and (not archive or archive.deleted_at):
            raise HTTPException(409, '对应归档资料已删除，请恢复后再从这份草稿创建待办')
        privacy = max([1] + [int(source.get('privacy_level', 1)) for source in snapshots.values()] +
                      [entry.privacy_level for entry in entries] + ([archive.privacy_level] if archive else []) +
                      ([project.privacy_level] if project else []))
        provenance = {'draft_uuid': draft.uuid, 'draft_revision': draft.revision,
            'draft_status_at_creation': draft.status, 'draft_content_hash': payload.content_hash,
            'draft_source_hash': draft.source_hash,
            'candidate_id': candidate_id, 'candidate_section': candidate['section'],
            'candidate_text': candidate['source_text'], 'source_uuid': source_uuid,
            'source_uuids': list(snapshots), 'archived_uuid': draft.archived_uuid,
            'source_versions_at_creation': {entry.uuid: entry.updated_at for entry in entries},
            'user_confirmed': True}
        description = ('来自审核稿的后续事项，需自行核对并执行。\n\n' + candidate['source_text'] +
                       '\n\n来源草稿 UUID：' + draft.uuid + '\n来源版本：' + str(draft.revision) +
                       ('\n关联原记录 UUID：' + source_uuid if source_uuid else ''))
        task = save(session, 'tasks', {'uuid': identity, 'title': title, 'description': description,
            'status': '待办', 'due_date': payload.due_date, 'project_id': project.id if project else None,
            'privacy_level': privacy, 'origin': 'manual', 'metadata_json': {'ai_followup': provenance}})
        audit(session, 'ai_followup_create', 'tasks', task.uuid, '人工从审核稿计划创建待办')
        return {'task': serialize(task, False), 'created': True}
