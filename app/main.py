from contextlib import closing
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import mimetypes
import os
import re
import secrets
import sqlite3
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse
import bleach
import markdown
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form, Query
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from starlette.middleware.trustedhost import TrustedHostMiddleware
from app.config import ROOT, HOME, settings, save_settings, directory
from app.database import engine, transaction, migrate, LOCK
from app.models import *
from app import extraction_models, semantic_models, workflow_models, capture_models
from app.schemas import SettingsInput, SCHEMAS, InputBase
from app.services.records import get, save, listing, serialize, audit, query_entries
from app.services import backup, exporting, importing
from app.services.statistics import statistics
from app.utils.process_lock import process_guard

(HOME / 'logs').mkdir(exist_ok=True)
logger = logging.getLogger('personal_database')
logger.setLevel(logging.INFO)
handler = RotatingFileHandler(HOME / 'logs/app.log', maxBytes=2_000_000, backupCount=5, encoding='utf-8')
handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
logger.addHandler(handler)
TOKEN = secrets.token_urlsafe(32)

@asynccontextmanager
async def lifespan(app):
    with process_guard(engine):
        backup.recover_pending_restore()
        backup.backup_before_upgrade()
        migrate()
        directory('attachment_dir')
        directory('backup_dir')
        try:
            backup.automatic_backup()
        except Exception as exc:
            logger.error('Automatic backup failed: %s', type(exc).__name__)
        from app.services import ai_worker
        ai_worker.start()
        try:
            yield
        finally:
            ai_worker.stop()
            engine.dispose()

app = FastAPI(title='个人知识、经验、案例与数字记忆数据库', version='1.1.0', lifespan=lifespan, docs_url=None, redoc_url=None)
from app.knowledge_routes import router as knowledge_router
app.include_router(knowledge_router)
from app.extraction_routes import router as extraction_router
from app.semantic_routes import router as semantic_router
app.include_router(extraction_router)
app.include_router(semantic_router)
from app.ai_routes import router as ai_router
app.include_router(ai_router)
from app.appearance_routes import router as appearance_router
app.include_router(appearance_router)
from app.answer_routes import router as answer_router
app.include_router(answer_router)
from app.speech_routes import router as speech_router
app.include_router(speech_router)
from app.capture_routes import router as capture_router
app.include_router(capture_router)
from app.action_routes import router as action_router
app.include_router(action_router)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', '[::1]', 'testserver'])

@app.middleware('http')
async def local_security(request, call_next):
    host = request.client.host if request.client else ''
    if host not in ('127.0.0.1', '::1', 'localhost', 'testclient'):
        return JSONResponse({'detail': '默认仅允许本机访问'}, status_code=403)
    origin = request.headers.get('origin')
    if origin and origin != str(request.base_url).rstrip('/'):
        return JSONResponse({'detail': '禁止跨站访问本地数据库'}, status_code=403)
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE') and request.url.path.startswith('/api/'):
        if not secrets.compare_digest(request.headers.get('x-local-token', ''), TOKEN):
            return JSONResponse({'detail': '页面凭证失效，请刷新后重试'}, status_code=403)
    length = request.headers.get('content-length', '0')
    if not length.isdigit() or int(length) > 512 * 1024**2:
        return JSONResponse({'detail': '单次上传最大 512 MB'}, status_code=413)
    response = await call_next(request)
    if response.status_code < 300 and request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        path = request.url.path
        capture_submit = request.method == 'POST' and re.fullmatch(r'/api/capture-drafts/[a-f0-9-]+/submit', path)
        if (capture_submit or path.startswith(('/api/entries', '/api/inbox', '/api/items/', '/api/knowledge', '/api/cases', '/api/attachments/'))) and not path.endswith('/extraction'):
            from app.services.ai_worker import record_changed
            record_changed()
            # Queue OCR after a successful upload, after its database commit.
            match = re.fullmatch(r'/api/entries/([a-f0-9-]+)/attachments', path)
            if match and request.method == 'POST':
                from app.services.ai_worker import submit_job
                with transaction() as s:
                    ids = list(s.scalars(select(Attachment.uuid).join(DailyEntry, Attachment.entry_id == DailyEntry.id)
                        .outerjoin(extraction_models.AttachmentExtraction, extraction_models.AttachmentExtraction.attachment_uuid == Attachment.uuid)
                        .where(DailyEntry.uuid == match.group(1), extraction_models.AttachmentExtraction.attachment_uuid.is_(None))))
                for uid in ids:
                    try:
                        submit_job('attachment_extract', {'uuid': uid})
                    except HTTPException:
                        pass
            if not path.endswith('/attachments'):
                # Bounded sweep coalesces saves and captures structured edits as well.
                try:
                    from app.semantic_routes import enqueue_index
                    from app.services.semantic import status as semantic_status
                    if semantic_status()['model_ready']:
                        from app.services.ai_worker import submit_job
                        submit_job('semantic_index', {})
                except Exception:
                    logger.warning('Index enqueue deferred')
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    response.headers['Cache-Control'] = 'no-store'
    return response

@app.exception_handler(ValidationError)
@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    return JSONResponse({'detail': '输入字段格式不正确，请检查日期、必填项和数值范围', 'fields': [str(e['loc']) for e in exc.errors()]}, status_code=422)

@app.exception_handler(IntegrityError)
async def conflict(request, exc):
    logger.warning('Database constraint conflict on %s', request.url.path.split('/')[2:3])
    return JSONResponse({'detail': '数据重复或关联关系有冲突，操作已回滚'}, status_code=409)

@app.exception_handler(Exception)
async def unexpected(request, exc):
    # Avoid exception text/SQL parameter dumps containing private record content.
    import traceback
    frames = ''.join(traceback.format_list(traceback.extract_tb(exc.__traceback__)))
    logger.error('Unhandled %s\n%s', type(exc).__name__, frames)
    return JSONResponse({'detail': '操作未完成，请重试或查看 logs/app.log。原事务已回滚。'}, status_code=500)

@app.get('/api/session')
def session_info():
    return {'token': TOKEN, 'settings': settings(), 'version': '1.1.0'}

@app.get('/docs', include_in_schema=False, response_class=HTMLResponse)
def api_docs():
    return '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>本地数据库 API</title><link rel="stylesheet" href="/static/vendor/swagger-ui.css"></head><body><div id="swagger-ui"></div><script src="/static/vendor/swagger-ui-bundle.js"></script><script src="/static/api-docs.js"></script></body></html>'''

@app.get('/api/settings')
def get_settings():
    return settings()

@app.put('/api/settings')
def update_settings(payload: SettingsInput):
    with LOCK:
        old = settings()
        data = payload.model_dump()
        for key in ('attachment_dir', 'backup_dir'):
            p = Path(data[key])
            resolved = (HOME / p).resolve()
            if not resolved.is_relative_to(HOME) or resolved == HOME:
                raise HTTPException(400, '为防止误写，目录需位于项目文件夹内')
            if data[key] != old[key]:
                source = directory(key)
                if any(source.iterdir()):
                    raise HTTPException(400, '已有数据时请保留原目录；迁移到新电脑可使用完整备份恢复')
                if resolved.exists() and any(resolved.iterdir()):
                    raise HTTPException(400, '请选择空目录')
            data[key] = resolved.relative_to(HOME).as_posix()
        ap, bp = [(HOME / data[k]).resolve() for k in ('attachment_dir', 'backup_dir')]
        protected = [ROOT / 'app', ROOT / 'database', HOME / '.venv', HOME / '.git', HOME / 'exports', HOME / 'logs', backup.db_path()]
        if ap.is_relative_to(bp) or bp.is_relative_to(ap) or any(p.is_relative_to(d) or d.is_relative_to(p) for p in (ap, bp) for d in protected):
            raise HTTPException(400, '附件和备份目录不能互相包含或覆盖程序及数据库目录')
        save_settings(data)
        directory('attachment_dir'); directory('backup_dir')
        return data

@app.get('/api/stats')
def stats(start: date | None = None, end: date | None = None):
    with transaction() as s:
        return statistics(s, start, end)

@app.get('/api/system')
def system_info():
    with transaction() as s:
        counts = {k: s.scalar(select(func.count()).select_from(v)) for k,v in ENTITIES.items()}
        counts['attachments'] = s.scalar(select(func.count(Attachment.id)))
    with closing(sqlite3.connect(backup.db_path())) as c:
        version = c.execute('SELECT version_num FROM alembic_version').fetchone()[0]
    from app.services.ai_settings import public_settings
    ai_cfg = public_settings()
    return {'version': '1.1.0', 'database_version': version, 'database_path': str(backup.db_path()),
            'database_size': backup.db_path().stat().st_size, 'attachment_path': str(directory('attachment_dir')),
            'attachment_size': sum(p.stat().st_size for p in directory('attachment_dir').rglob('*') if p.is_file()),
            'counts': counts, 'backups': backup.list_backups(), 'ai_status': ('已停用' if ai_cfg['provider'] == 'disabled' else ai_cfg['provider'] + ' · ' + ai_cfg['model'])}

@app.get('/api/calendar')
def calendar(month: str = Query(pattern=r'^\d{4}-\d{2}$')):
    with transaction() as s:
        rows = s.execute(select(DailyEntry.date, DailyEntry.category, func.count()).where(DailyEntry.deleted_at.is_(None), func.substr(DailyEntry.date,1,7) == month).group_by(DailyEntry.date, DailyEntry.category))
        return [{'date': d.isoformat(), 'category': c, 'count': n} for d,c,n in rows]

@app.get('/api/timeline')
def timeline():
    with transaction() as s:
        return [{'month': m, 'count': n} for m,n in s.execute(select(func.substr(DailyEntry.date,1,7),func.count()).where(DailyEntry.deleted_at.is_(None)).group_by(func.substr(DailyEntry.date,1,7)).order_by(func.substr(DailyEntry.date,1,7).desc()))]

@app.get('/api/search')
@app.get('/api/entries')
def entries(request: Request, page: int = 1, size: int = 30):
    with transaction() as s:
        return listing(s, dict(request.query_params), page, size)

@app.post('/api/entries/{uid}/restore')
def restore_entry(uid: str):
    with transaction() as s:
        obj = get(s, DailyEntry, uid)
        obj.deleted_at = None
        audit(s, '恢复', 'entries', uid)
        return {'ok': True}

class FlagsInput(InputBase):
    is_favorite: bool | None = None
    is_archived: bool | None = None

@app.patch('/api/items/{uid}/flags')
def item_flags(uid: str, payload: FlagsInput):
    with transaction() as s:
        obj = get(s, DailyEntry, uid)
        for field,value in payload.model_dump(exclude_none=True).items():
            setattr(obj,field,value)
        audit(s,'修改收藏或归档','entries',uid)
        s.flush()
        return serialize(obj)

@app.delete('/api/entries/{uid}/permanent')
def purge_entry(uid: str):
    with transaction() as s:
        obj = get(s, DailyEntry, uid)
        if obj.deleted_at is None:
            raise HTTPException(400, '请先移入回收站')
        files = [backup.safe_attachment(a.file_path) for a in obj.attachments]
        s.delete(obj)
        audit(s, '彻底删除', 'entries', uid)
    for file in files:
        file.unlink(missing_ok=True)
    return {'ok': True}

@app.post('/api/entries/{uid}/attachments')
def upload_attachment(uid: str, file: UploadFile = File(...), description: str = Form('')):
    with transaction() as s:
        entry = get(s, DailyEntry, uid)
        original = (file.filename or 'attachment').replace('\\', '/').split('/')[-1][:500]
        suffix = Path(original).suffix.lower()
        if not re.fullmatch(r'\.[a-z0-9]{1,12}', suffix):
            suffix = '.bin'
        stored = str(uuid.uuid4()) + suffix
        relative = f'{date.today():%Y/%m}/' + stored
        path = backup.safe_attachment(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        sha = hashlib.sha256(); size = 0
        try:
            with path.open('xb') as output:
                while chunk := file.file.read(1024 * 1024):
                    size += len(chunk)
                    if size > 100 * 1024**2:
                        raise HTTPException(413, '单个附件最大 100 MB')
                    sha.update(chunk); output.write(chunk)
            obj = Attachment(original_filename=original, stored_filename=stored, file_path=relative,
                             file_type=file.content_type or 'application/octet-stream', file_size=size,
                             sha256=sha.hexdigest(), entry_id=entry.id, description=description[:10000])
            s.add(obj); s.flush()
            audit(s, '上传附件', 'attachments', obj.uuid)
            result = serialize(obj)
        except Exception:
            path.unlink(missing_ok=True)
            raise
    return result

@app.get('/api/attachments/{uid}')
def download_attachment(uid: str):
    with transaction() as s:
        a = get(s, Attachment, uid)
        path = backup.safe_attachment(a.file_path)
        if not path.is_file():
            raise HTTPException(404, '附件文件缺失，请运行完整性检查')
        return FileResponse(path, filename=a.original_filename, media_type='application/octet-stream')

@app.put('/api/attachments/{uid}')
def describe_attachment(uid: str, payload: dict):
    with transaction() as s:
        obj = get(s, Attachment, uid)
        obj.description = str(payload.get('description', ''))[:10000]
        audit(s, '修改附件说明', 'attachments', uid)
        return serialize(obj)

@app.delete('/api/attachments/{uid}')
def delete_attachment(uid: str):
    with transaction() as s:
        obj = get(s, Attachment, uid)
        path = backup.safe_attachment(obj.file_path)
        s.delete(obj)
        audit(s, '删除附件', 'attachments', uid)
    path.unlink(missing_ok=True)
    return {'ok': True}

@app.post('/api/entries/{uid}/links')
def add_link(uid: str, payload: dict):
    with transaction() as s:
        entry = get(s, DailyEntry, uid)
        url = str(payload.get('url',''))
        if urlparse(url).scheme not in ('http', 'https') or not urlparse(url).netloc:
            raise HTTPException(400, '链接必须以 http:// 或 https:// 开头')
        obj = Link(entry_id=entry.id, url=url, title=str(payload.get('title',''))[:500], description=str(payload.get('description','')))
        s.add(obj); s.flush()
        audit(s, '添加链接', 'links', obj.uuid)
        return serialize(obj)

@app.delete('/api/links/{uid}')
def remove_link(uid: str):
    with transaction() as s:
        s.delete(get(s, Link, uid)); audit(s, '删除链接', 'links', uid)
    return {'ok':True}

@app.post('/api/markdown')
def preview_markdown(payload: dict):
    rendered = markdown.markdown(str(payload.get('content',''))[:2_000_000], extensions=['fenced_code','tables','nl2br','sane_lists'])
    return {'html': bleach.clean(rendered, tags=set(bleach.sanitizer.ALLOWED_TAGS) | {'p','h1','h2','h3','h4','h5','h6','pre','hr','br','table','thead','tbody','tr','td','th','img'}, attributes={'a':['href','title'], 'img':['src','alt','title']}, protocols=['http','https'], strip=True)}

@app.post('/api/export')
def export(payload: dict):
    fmt = payload.get('format', 'context')
    if fmt not in ('context','json','jsonl','csv','markdown','txt','db','sql','full-json'):
        raise HTTPException(400, '导出格式不支持')
    with transaction() as s:
        path = exporting.export_database(s, 'json' if fmt == 'full-json' else fmt) if fmt in ('db','sql','full-json') else exporting.export_records(s, payload.get('filters', {}), fmt, payload.get('options', {}))
    return {'url': '/api/downloads/' + path.name, 'name': path.name}

@app.get('/api/downloads/{name}')
def download_export(name: str):
    base = (HOME / 'exports').resolve(); path = (base / name).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise HTTPException(404, '文件不存在')
    return FileResponse(path, filename=path.name, media_type='application/octet-stream')

@app.post('/api/import/preview')
def import_preview(file: UploadFile = File(...), mapping: str = Form('{}')):
    raw = file.file.read(20 * 1024**2 + 1)
    if len(raw) > 20 * 1024**2:
        raise HTTPException(413, '单次导入最大 20 MB')
    try:
        mapping_data = json.loads(mapping)
        if not isinstance(mapping_data, dict):
            raise ValueError()
    except ValueError:
        raise HTTPException(400, '字段映射须为 JSON 对象')
    with transaction() as s:
        return importing.preview(s, file.filename or '', raw, mapping_data)

@app.post('/api/import/confirm')
def import_confirm(payload: dict):
    token = payload.get('token','')
    with transaction() as s:
        result = importing.confirm(s, token, payload.get('strategy','skip'))
    importing.PREVIEWS.pop(token, None)
    return result

@app.post('/api/backups')
def make_backup():
    path = backup.create_backup()
    return {'name':path.name}

@app.get('/api/backups')
def backups():
    return backup.list_backups()

@app.get('/api/backups/{name}')
def download_backup(name: str):
    base = directory('backup_dir'); path = (base / name).resolve()
    if path.parent != base or path.suffix != '.zip' or not path.is_file():
        raise HTTPException(404, '备份不存在')
    return FileResponse(path, filename=path.name, media_type='application/zip')

@app.post('/api/backup-restore')
def restore_backup(payload: dict):
    name = payload.get('name',''); base = directory('backup_dir'); path = (base / name).resolve()
    if path.parent != base or not path.is_file():
        raise HTTPException(404, '备份不存在')
    if payload.get('confirmation') != '恢复备份':
        raise HTTPException(400, '请明确确认恢复备份')
    return backup.restore_backup(path)

@app.post('/api/backup-upload')
def backup_upload(file: UploadFile = File(...)):
    import tempfile
    with LOCK, tempfile.TemporaryDirectory(dir=HOME) as tmp:
        path = Path(tmp)/'upload.zip'
        size = 0
        with path.open('wb') as out:
            while chunk := file.file.read(1024**2):
                size += len(chunk)
                if size > 500 * 1024**2:
                    raise HTTPException(413, '上传备份最大 500 MB；更大的请手动放入 backups 目录')
                out.write(chunk)
        try:
            backup.validate_archive(path, Path(tmp)/'validate')
        except Exception:
            raise HTTPException(400, '备份无效或版本不兼容')
        dest = directory('backup_dir') / f'backup_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}_uploaded.zip'
        import shutil
        shutil.copyfile(path,dest)
    return {'name':dest.name}

@app.post('/api/integrity')
def check_integrity():
    return backup.integrity()

@app.get('/api/audit')
def audit_logs(page: int = 1):
    with transaction() as s:
        return [serialize(x) for x in s.scalars(select(AuditLog).order_by(AuditLog.id.desc()).offset((max(page,1)-1)*100).limit(100))]

@app.get('/api/schema')
def schema():
    return {'markdown': exporting.schema_markdown()}

@app.get('/api/{kind}/{uid}/overview')
def overview(kind: str, uid: str, page: int = 1, size: int = 30):
    if kind not in ('projects', 'people'):
        raise HTTPException(404, '页面不存在')
    model = ENTITIES[kind]
    with transaction() as s:
        obj = get(s, model, uid)
        key = 'project' if kind == 'projects' else 'person'
        page=max(1,page); size=min(max(1,size),100); offset=(page-1)*size
        related_entries = listing(s, {key: uid}, page, size)
        fk = 'project_id' if kind == 'projects' else 'person_id'
        entry_query = query_entries({key: uid})
        from app.models import entry_people, entry_projects
        relation = entry_people if kind == 'projects' else entry_projects
        target = Person if kind == 'projects' else Project
        rid = relation.c.person_id if kind == 'projects' else relation.c.project_id
        others_query=select(target).where(target.id.in_(select(rid).where(relation.c.entry_id.in_(entry_query.with_only_columns(DailyEntry.id)))))
        others = list(s.scalars(others_query.limit(size).offset(offset)))
        task_query=select(Task).where(getattr(Task,fk)==obj.id)
        event_query=select(Event).where(getattr(Event,fk)==obj.id)
        attachment_query=select(Attachment).where(Attachment.entry_id.in_(entry_query.with_only_columns(DailyEntry.id)))
        return {'object':serialize(obj),'entries':related_entries,
                'related':[serialize(v) for v in others],
                'tasks':[serialize(v) for v in s.scalars(task_query.limit(size).offset(offset))],
                'events':[serialize(v) for v in s.scalars(event_query.limit(size).offset(offset))],
                'attachments':[serialize(v) for v in s.scalars(attachment_query.limit(size).offset(offset))],
                'page':page,'size':size,'totals':{key:s.scalar(select(func.count()).select_from(q.subquery())) for key,q in [('related',others_query),('tasks',task_query),('events',event_query),('attachments',attachment_query)]}}

@app.post('/api/projects/{uid}/review')
def review_project(uid: str, payload: dict):
    with transaction() as s:
        get(s,Project,uid)
        data = payload | {'date':date.today().isoformat(),'review_type':'project','target_type':'projects','project_uuid':uid}
        data.pop('target_uuid',None)
        return serialize(save(s,'reviews',data))

# Typed create/update endpoints preserve full Pydantic schemas in OpenAPI.
def register_entity(kind, model):
    schema = SCHEMAS[kind]
    def create(payload: schema):
        with transaction() as s:
            return serialize(save(s, kind, payload.model_dump(mode='json')))
    def update(uid: str, payload: schema):
        with transaction() as s:
            return serialize(save(s, kind, payload.model_dump(mode='json'), uid))
    def read(uid: str):
        with transaction() as s:
            return serialize(get(s, model, uid))
    def remove(uid: str):
        with transaction() as s:
            obj = get(s, model, uid)
            if kind == 'entries':
                obj.deleted_at = now()
            elif kind == 'conversations' and obj.entry_uuid:
                get(s,DailyEntry,obj.entry_uuid).deleted_at=now()
            else:
                s.delete(obj)
            audit(s, '删除', kind, uid)
        return {'ok': True}
    def list_items(request: Request, q: str = '', page: int = 1, size: int = 100):
        with transaction() as s:
            query = select(model)
            if kind=='conversations':
                query=query.where(model.entry_uuid.in_(select(DailyEntry.uuid).where(DailyEntry.deleted_at.is_(None))))
            field = getattr(model, 'name', getattr(model, 'title', None))
            if q and field is not None:
                query = query.where(field.contains(q, autoescape=True))
            if request.query_params.get('internal_id'):
                try:
                    query=query.where(model.id==int(request.query_params['internal_id']))
                except ValueError:
                    raise HTTPException(400,'内部编号无效')
            for key in ('domain_id','parent_id','project_id','person_id','status','period'):
                if key in request.query_params and hasattr(model,key):
                    value=request.query_params[key]
                    if value=='null':
                        query=query.where(getattr(model,key).is_(None))
                    elif value:
                        query=query.where(getattr(model,key)==value)
            total = s.scalar(select(func.count()).select_from(query.subquery()))
            return {'items':[serialize(v) for v in s.scalars(query.order_by(model.id.desc()).offset((max(page,1)-1)*min(max(size,1),500)).limit(min(max(size,1),500)))], 'total':total}
    app.post('/api/' + kind, name='create_' + kind)(create)
    app.put('/api/' + kind + '/{uid}', name='update_' + kind)(update)
    app.get('/api/' + kind + '/{uid}', name='read_' + kind)(read)
    app.delete('/api/' + kind + '/{uid}', name='delete_' + kind)(remove)
    if kind != 'entries':
        app.get('/api/' + kind, name='list_' + kind)(list_items)

for kind, model in ENTITIES.items():
    register_entity(kind, model)

app.mount('/static', StaticFiles(directory=ROOT / 'app/static'), name='static')

@app.get('/', response_class=HTMLResponse)
def index():
    return (ROOT / 'app/static/index.html').read_text(encoding='utf-8')
