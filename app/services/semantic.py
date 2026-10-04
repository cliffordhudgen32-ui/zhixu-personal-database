"""Real Chinese ONNX embeddings, local cosine retrieval and auditable evidence.

Preparing weights is the only network operation. Indexing and searching never
send personal text to a provider. Model loading/inference run outside DB LOCK.
"""
from array import array
from hashlib import sha256
import importlib.util
from importlib.metadata import version as package_version
import json
import math
import os
from pathlib import Path
import re
import sys
import ssl
from threading import RLock

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import lazyload

from app.config import HOME
from app.database import LOCK, Session
from app.knowledge_models import ASSETS
from app.models import Attachment, DailyEntry, now
from app.semantic_models import EmbeddingChunk
from app.services.records import query_entries

DEFAULT_MODEL = 'BAAI/bge-small-zh-v1.5'
CHUNKER_VERSION = 'evidence-char320-overlap48-v1'
MODEL_CACHE = HOME / 'models'
MANIFEST = MODEL_CACHE / 'semantic_model.json'
MODEL_LOCK = RLock()
_MODEL = None
_MODEL_VERSION = None
_LAST_ERROR = ''
_STATE = 'not_ready'


class SemanticUnavailable(RuntimeError):
    pass


def _manifest():
    try:
        data = json.loads(MANIFEST.read_text(encoding='utf-8'))
        model_file = Path(data['model_file']).resolve()
        if data.get('model') == DEFAULT_MODEL and model_file.is_relative_to(MODEL_CACHE.resolve()) and model_file.is_file():
            return data
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def prepare_model(allow_download=True):
    """Explicitly download only public model weights, or load an existing cache."""
    global _MODEL, _MODEL_VERSION, _LAST_ERROR, _STATE
    with MODEL_LOCK:
        if _MODEL is not None:
            return {'state': 'ready', 'model': DEFAULT_MODEL, 'model_version': _MODEL_VERSION, 'model_ready': True}
        if not importlib.util.find_spec('fastembed'):
            _STATE = 'dependency_missing'
            _LAST_ERROR = '尚未安装本地语义模型依赖，请运行依赖安装后准备中文模型。'
            raise SemanticUnavailable(_LAST_ERROR)
        MODEL_CACHE.mkdir(parents=True, exist_ok=True)
        _STATE = 'downloading' if allow_download else 'loading'
        try:
            if allow_download and sys.platform == 'win32':
                # Honor Windows' trusted enterprise/proxy CAs; TLS verification
                # remains enabled instead of bypassing a certificate failure.
                import httpx
                from huggingface_hub import set_client_factory
                from huggingface_hub.utils._http import hf_request_event_hook
                context = ssl.create_default_context()
                set_client_factory(lambda: httpx.Client(verify=context, follow_redirects=True,
                    timeout=httpx.Timeout(60, connect=20), event_hooks={'request': [hf_request_event_hook]}))
            from fastembed import TextEmbedding
            model = TextEmbedding(model_name=DEFAULT_MODEL, cache_dir=str(MODEL_CACHE),
                providers=['CPUExecutionProvider'], threads=max(1, min(2, os.cpu_count() or 1)),
                local_files_only=not allow_download)
            description = model.model.model_description
            model_file = (Path(model.model._model_dir) / description.model_file).resolve()
            if not model_file.is_relative_to(MODEL_CACHE.resolve()):
                raise ValueError('模型缓存位置无效')
            digest = sha256()
            with model_file.open('rb') as stream:
                for piece in iter(lambda: stream.read(2 * 1024**2), b''):
                    digest.update(piece)
            weights_hash = digest.hexdigest()
            model_version = DEFAULT_MODEL + ':' + weights_hash[:24] + ':fastembed-' + package_version('fastembed') + ':' + CHUNKER_VERSION
            # Verify that a usable local inference session exists before claiming ready.
            _normal_vector(next(iter(model.query_embed('本地模型准备完成'))))
            data = dict(model=DEFAULT_MODEL, model_version=model_version, model_file=str(model_file),
                        weights_sha256=weights_hash, dimension=int(model.embedding_size), prepared_at=now())
            temporary = MANIFEST.with_suffix('.tmp')
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(MANIFEST)
            _MODEL, _MODEL_VERSION = model, model_version
            _STATE, _LAST_ERROR = 'ready', ''
            return dict(state='ready', model_ready=True, **data)
        except SemanticUnavailable:
            raise
        except Exception as exc:
            _STATE = 'model_not_ready'
            _LAST_ERROR = ('中文语义模型尚未准备，请先下载模型。' if not allow_download else
                           '中文模型准备失败，请检查网络和 Windows VC++ 运行库后重试。')
            raise SemanticUnavailable(_LAST_ERROR) from exc


def get_embedder():
    if _MODEL is None:
        if not _manifest():
            raise SemanticUnavailable('中文语义模型尚未准备，当前使用关键词搜索。请先准备模型并生成索引。')
        prepare_model(allow_download=False)
    return _MODEL


def _identity(embedder):
    model_name = str(getattr(embedder, 'model_name', DEFAULT_MODEL))
    model_version = str(getattr(embedder, 'model_version', None) or _MODEL_VERSION or model_name + ':' + CHUNKER_VERSION)
    return model_name, model_version


def _normal_vector(vector):
    values = [float(value) for value in vector]
    if not values or any(not math.isfinite(value) for value in values):
        raise ValueError('本地模型返回无效向量')
    norm = math.sqrt(sum(value * value for value in values))
    if norm <= 0:
        raise ValueError('本地模型返回空向量')
    return [value / norm for value in values]


def _pack(values):
    result = array('f', values)
    if sys.byteorder != 'little':
        result.byteswap()
    return result.tobytes()


def _unpack(value):
    result = array('f')
    result.frombytes(value)
    if sys.byteorder != 'little':
        result.byteswap()
    return result


def _split(text, size=320, overlap=48):
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            boundary = max(text.rfind(separator, start + size // 2, end) for separator in ('\n', '。', '！', '？', '. ', '; '))
            if boundary >= start + size // 2:
                end = boundary + 1
        if text[start:end].strip():
            yield start, end, text[start:end]
        if end == len(text):
            break
        start = max(start + 1, end - overlap)


def attachment_texts(session, entry_id):
    """Adapter for the extraction service: pages carry absolute text offsets."""
    try:
        from app.extraction_models import AttachmentExtraction
    except ImportError:
        return []
    query = select(Attachment, AttachmentExtraction).join(AttachmentExtraction,
        AttachmentExtraction.attachment_uuid == Attachment.uuid).where(Attachment.entry_id == entry_id,
        AttachmentExtraction.source_sha256 == Attachment.sha256,
        AttachmentExtraction.status.in_(('ready', 'done', 'completed', 'success', 'partial')))
    return [{'attachment_uuid': attachment.uuid, 'sha256': attachment.sha256,
             'label': attachment.original_filename, 'text': extracted.text,
             'pages': extracted.pages, 'parser_version': extracted.parser_version,
             'truncated': extracted.truncated} for attachment, extracted in session.execute(query)]


def snapshot_entry(session, uid, attachment_adapter=None):
    entry = session.scalar(select(DailyEntry).options(lazyload('*')).where(DailyEntry.uuid == uid))
    if not entry or entry.deleted_at:
        return None
    sources = []
    for field in ('title', 'content', 'original_content', 'summary', 'normalized_content', 'ai_summary', 'notes'):
        value = getattr(entry, field) or ''
        if field == 'normalized_content':
            value = re.sub(r'\n\n<!-- pld-structured-fields:[0-9a-fA-F-]{36} -->\n.*?\n<!-- /pld-structured-fields -->\Z', '', value, flags=re.DOTALL)
        if value.strip() and not (field == 'original_content' and value == entry.content):
            sources.append(dict(field=field, text=value, attachment_uuid=None, page=None, label=field, start=0))
    if entry.item_type in ASSETS:
        details = session.scalar(select(ASSETS[entry.item_type]).where(ASSETS[entry.item_type].uuid == uid))
        if details:
            for column in details.__table__.columns:
                value = getattr(details, column.name)
                if column.name in ('uuid', 'source_case_uuid', 'last_used_at', 'last_reviewed_at', 'last_verified_at'):
                    continue
                if isinstance(value, str) and value.strip():
                    sources.append(dict(field='details.' + column.name, text=value, attachment_uuid=None,
                                        page=None, label=column.name, start=0))
    adapter = attachment_adapter or attachment_texts
    attachments = adapter(session, entry.id)
    for attachment in attachments:
        text = attachment.get('text') or ''
        pages = attachment.get('pages') or [{'start': 0, 'end': len(text), 'page': None}]
        for page in pages:
            start = max(0, min(len(text), int(page.get('start', 0))))
            end = max(start, min(len(text), int(page.get('end', len(text)))))
            if text[start:end].strip():
                sources.append(dict(field='attachment', text=text[start:end], attachment_uuid=attachment['attachment_uuid'],
                    attachment_sha256=attachment['sha256'], page=page.get('page'), label=attachment.get('label', ''), start=start))
    fingerprint = dict(uuid=uid, title=entry.title, item_type=entry.item_type, date=entry.date.isoformat(),
                       sources=sources, attachment_versions=[(v['attachment_uuid'], v['sha256'], v.get('parser_version')) for v in attachments])
    digest = sha256(json.dumps(fingerprint, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
    return dict(uuid=uid, title=entry.title, updated_at=entry.updated_at, source_hash=digest,
                sources=sources, warnings=['部分附件提取内容达到长度上限。'] if any(v.get('truncated') for v in attachments) else [])


def index_entry(uid, *, embedder=None, session_factory=Session, attachment_adapter=None, force=False, stopped=lambda: False):
    """Snapshot → infer without a database lock → recheck → atomically replace."""
    if stopped():
        raise InterruptedError()
    with session_factory() as session:
        snapshot = snapshot_entry(session, uid, attachment_adapter)
    if snapshot is None:
        with LOCK, session_factory.begin() as session:
            if stopped():
                raise InterruptedError()
            session.execute(delete(EmbeddingChunk).where(EmbeddingChunk.entry_uuid == uid))
        return {'state': 'skipped', 'uuid': uid, 'chunks': 0, 'reason': '记录不存在或已进入回收站'}
    embedder = embedder or get_embedder()
    model_name, model_version = _identity(embedder)
    if not force:
        with session_factory() as session:
            existing = session.scalar(select(EmbeddingChunk).where(EmbeddingChunk.entry_uuid == uid,
                EmbeddingChunk.model_version == model_version, EmbeddingChunk.source_hash == snapshot['source_hash'],
                EmbeddingChunk.entry_updated_at == snapshot['updated_at']).limit(1))
            if existing:
                return {'state': 'unchanged', 'uuid': uid, 'source_hash': snapshot['source_hash'], 'chunks': 0}
    chunks, documents = [], []
    for source in snapshot['sources']:
        for start, end, evidence in _split(source['text']):
            if len(chunks) >= 2000:
                break
            chunks.append(dict(field=source['field'], attachment_uuid=source['attachment_uuid'],
                attachment_sha256=source.get('attachment_sha256'), page=source['page'], source_label=source['label'][:500],
                start_offset=source['start'] + start, end_offset=source['start'] + end, evidence=evidence))
            documents.append(snapshot['title'][:60] + '\n' + source['field'] + '：\n' + evidence)
    if len(chunks) == 2000:
        snapshot['warnings'].append('记录内容过长，本次索引保留前 2000 个证据片段。')
    embed = getattr(embedder, 'passage_embed', None) or embedder.embed
    with MODEL_LOCK:
        if stopped():
            raise InterruptedError()
        vectors = [_normal_vector(value) for value in embed(documents, batch_size=16)]
    if len(vectors) != len(chunks) or (vectors and any(len(value) != len(vectors[0]) for value in vectors)):
        raise ValueError('本地模型返回的向量数量或维度不一致')
    with LOCK, session_factory.begin() as session:
        if stopped():
            raise InterruptedError()
        current = snapshot_entry(session, uid, attachment_adapter)
        if not current or current['updated_at'] != snapshot['updated_at'] or current['source_hash'] != snapshot['source_hash']:
            return {'state': 'stale', 'uuid': uid, 'chunks': 0, 'reason': '索引计算期间原资料已改变，请重新索引'}
        session.execute(delete(EmbeddingChunk).where(EmbeddingChunk.entry_uuid == uid))
        session.add_all(EmbeddingChunk(entry_uuid=uid, model=model_name, model_version=model_version,
            source_hash=snapshot['source_hash'], entry_updated_at=snapshot['updated_at'], chunk_index=index,
            dimension=len(vector), vector=_pack(vector), **chunk)
            for index, (chunk, vector) in enumerate(zip(chunks, vectors)))
    return {'state': 'indexed', 'uuid': uid, 'chunks': len(chunks), 'source_hash': snapshot['source_hash'],
            'model_version': model_version, 'warnings': snapshot['warnings']}


def index_all(*, embedder=None, session_factory=Session, force=False, progress=None, stopped=lambda: False):
    if stopped():
        raise InterruptedError()
    embedder = embedder or get_embedder()
    cursor = completed = stale = 0
    with session_factory() as session:
        maximum_id = session.scalar(select(func.max(DailyEntry.id))) or 0
    while True:
        with session_factory() as session:
            rows = list(session.execute(select(DailyEntry.id, DailyEntry.uuid).where(DailyEntry.id > cursor, DailyEntry.id <= maximum_id,
                DailyEntry.deleted_at.is_(None)).order_by(DailyEntry.id).limit(100)))
        if not rows:
            break
        for entry_id, uid in rows:
            if stopped():
                raise InterruptedError()
            outcome = index_entry(uid, embedder=embedder, session_factory=session_factory, force=force, stopped=stopped)
            completed += outcome['state'] in ('indexed', 'unchanged')
            stale += outcome['state'] == 'stale'
            cursor = entry_id
            if progress:
                progress({'indexed_entries': completed, 'stale_entries': stale, 'last_uuid': uid})
    return {'state': 'completed', 'indexed_entries': completed, 'stale_entries': stale}


def _visible_chunks(filters, include_sensitive, model_version):
    entries = query_entries({key: value for key, value in filters.items() if key not in ('q', 'trash')},
                            include_sensitive=include_sensitive).with_only_columns(DailyEntry.uuid)
    return select(EmbeddingChunk, DailyEntry.title, DailyEntry.item_type, DailyEntry.date).join(DailyEntry,
        DailyEntry.uuid == EmbeddingChunk.entry_uuid).where(EmbeddingChunk.entry_uuid.in_(entries),
        EmbeddingChunk.model_version == model_version, EmbeddingChunk.entry_updated_at == DailyEntry.updated_at,
        or_(EmbeddingChunk.attachment_uuid.is_(None), EmbeddingChunk.attachment_uuid.in_(select(Attachment.uuid).where(
            Attachment.sha256 == EmbeddingChunk.attachment_sha256, Attachment.entry_id == DailyEntry.id))))


def _keyword_search(query, filters, include_sensitive, limit, warning, session_factory):
    # A transparent fallback is useful before installing or preparing the model.
    query_entries_statement = query_entries({key: value for key, value in filters.items() if key not in ('q', 'trash')},
                                           include_sensitive=include_sensitive)
    for term in query.split()[:20]:
        query_entries_statement = query_entries_statement.where(or_(*(column.contains(term, autoescape=True)
            for column in (DailyEntry.title, DailyEntry.content, DailyEntry.summary, DailyEntry.normalized_content))))
    with session_factory() as session:
        total = session.scalar(select(func.count()).select_from(query_entries_statement.subquery()))
        rows = session.execute(query_entries_statement.with_only_columns(DailyEntry.uuid, DailyEntry.title,
            DailyEntry.item_type, DailyEntry.date, DailyEntry.content, DailyEntry.summary).order_by(DailyEntry.updated_at.desc()).limit(limit))
        items = [dict(uuid=row.uuid, title=row.title, item_type=row.item_type, date=row.date.isoformat(), score=None,
                      excerpt=(row.content or row.summary or row.title)[:320],
                      field='content' if row.content else 'summary' if row.summary else 'title', attachment_uuid=None, page=None,
                      source_url='/api/entries/' + row.uuid) for row in rows]
    return {'items': items, 'retrieval_method': 'keyword', 'warnings': [warning], 'total': total}


def search(query, filters=None, *, limit=20, include_sensitive=False, min_score=0.35, embedder=None, session_factory=Session):
    query = str(query).strip()
    if not query:
        return {'items': [], 'retrieval_method': 'semantic', 'warnings': [], 'total': 0}
    if len(query) > 2000:
        raise ValueError('搜索内容最多 2000 字')
    limit = max(1, min(100, int(limit)))
    filters = filters or {}
    try:
        embedder = embedder or get_embedder()
        model_name, model_version = _identity(embedder)
        with MODEL_LOCK:
            query_embed = getattr(embedder, 'query_embed', None) or embedder.embed
            query_vector = _normal_vector(next(iter(query_embed([query]))))
    except SemanticUnavailable as exc:
        return _keyword_search(query, filters, include_sensitive, limit, str(exc), session_factory)
    try:
        import numpy as np
    except ImportError:
        np = None
    pool, matched, seen_chunks = {}, set(), 0
    with session_factory() as session:
        statement = _visible_chunks(filters, include_sensitive, model_version)
        for batch in session.execute(statement.execution_options(yield_per=256)).partitions(256):
            valid = [(chunk, title, item_type, date_value) for chunk, title, item_type, date_value in batch
                     if chunk.dimension == len(query_vector) and len(chunk.vector) == chunk.dimension * 4]
            if not valid:
                continue
            seen_chunks += len(valid)
            if np is not None:
                matrix = np.stack([np.frombuffer(item[0].vector, dtype='<f4') for item in valid])
                scores = matrix @ np.asarray(query_vector, dtype=np.float32)
            else:
                scores = [sum(x * y for x, y in zip(_unpack(item[0].vector), query_vector)) for item in valid]
            for (chunk, title, item_type, date_value), score in zip(valid, scores):
                score = float(score)
                if not math.isfinite(score) or score < min_score:
                    continue
                matched.add(chunk.entry_uuid)
                previous = pool.get(chunk.entry_uuid)
                if previous and previous['score'] >= score:
                    continue
                pool[chunk.entry_uuid] = dict(uuid=chunk.entry_uuid, title=title, item_type=item_type, date=date_value.isoformat(),
                    score=round(max(-1.0, min(1.0, score)), 6), excerpt=chunk.evidence, field=chunk.field,
                    attachment_uuid=chunk.attachment_uuid, page=chunk.page, source_label=chunk.source_label,
                    start_offset=chunk.start_offset, end_offset=chunk.end_offset, source_hash=chunk.source_hash,
                    source_url='/api/attachments/' + chunk.attachment_uuid if chunk.attachment_uuid else '/api/entries/' + chunk.entry_uuid,
                    _version=chunk.entry_updated_at, _attachment_hash=chunk.attachment_sha256)
                if len(pool) > limit * 3:
                    pool.pop(min(pool, key=lambda uid: pool[uid]['score']))
    if not seen_chunks:
        return _keyword_search(query, filters, include_sensitive, limit,
            '当前筛选没有可用的最新向量索引，已使用关键词搜索。请生成或更新语义索引。', session_factory)
    # Recheck privacy, deletion and source revisions after the long cosine scan.
    with session_factory() as session:
        current_query = query_entries({key: value for key, value in filters.items()
            if key not in ('q', 'trash')}, include_sensitive=include_sensitive).where(DailyEntry.uuid.in_(pool))
        current = {row.uuid: row for row in session.execute(current_query.with_only_columns(
            DailyEntry.id, DailyEntry.uuid, DailyEntry.updated_at))}
        attachments = {row.uuid: row for row in session.execute(select(Attachment.uuid, Attachment.sha256, Attachment.entry_id).where(
            Attachment.uuid.in_([item['attachment_uuid'] for item in pool.values() if item['attachment_uuid']])))}
        items = []
        for item in sorted(pool.values(), key=lambda item: item['score'], reverse=True):
            row = current.get(item['uuid'])
            if not row or row.updated_at != item.pop('_version'):
                continue
            if item['attachment_uuid']:
                attachment = attachments.get(item['attachment_uuid'])
                if not attachment or attachment.sha256 != item['_attachment_hash'] or attachment.entry_id != row.id:
                    continue
            item.pop('_attachment_hash')
            items.append(item)
            if len(items) == limit:
                break
    return {'items': items, 'retrieval_method': 'semantic', 'warnings': [], 'total': len(matched),
            'model': model_name, 'model_version': model_version, 'score_metric': 'cosine_similarity'}


def status(session_factory=Session):
    manifest = _manifest()
    model_version = _MODEL_VERSION or (manifest or {}).get('model_version')
    with session_factory() as session:
        total = session.scalar(select(func.count(DailyEntry.id)).where(DailyEntry.deleted_at.is_(None))) or 0
        indexed_query = select(func.count(func.distinct(EmbeddingChunk.entry_uuid))).join(DailyEntry,
            DailyEntry.uuid == EmbeddingChunk.entry_uuid).where(DailyEntry.deleted_at.is_(None),
            EmbeddingChunk.entry_updated_at == DailyEntry.updated_at)
        if model_version:
            indexed_query = indexed_query.where(EmbeddingChunk.model_version == model_version)
        indexed = (session.scalar(indexed_query) or 0) if model_version else 0
    ready = _MODEL is not None or bool(manifest and importlib.util.find_spec('fastembed'))
    state = 'ready' if ready and _STATE not in ('loading', 'downloading') else _STATE
    if not ready and not importlib.util.find_spec('fastembed'):
        state = 'dependency_missing'
    return {'state': state,
            'model': DEFAULT_MODEL, 'model_ready': ready, 'model_version': model_version,
            'indexed_entries': indexed, 'total_entries': total, 'pending': max(0, total - indexed), 'last_error': _LAST_ERROR,
            'model_directory': str(MODEL_CACHE), 'local_only': True}
