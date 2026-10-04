"""Local bounded attachment parsing, isolated from API/database writer locks."""
import hashlib
import json
import logging
import math
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

PARSER_VERSION = 'local-v1'
MAX_FILE_BYTES = 50 * 1024**2
MAX_ARCHIVE_BYTES = 200 * 1024**2
MAX_XML_BYTES = 20 * 1024**2
MAX_TEXT_CHARS = 2_000_000
MAX_PAGES = 100
MAX_OCR_PAGES = 30
MAX_CELLS = 100_000
MAX_PIXELS = 25_000_000
TIMEOUT_SECONDS = 90
SUPPORTED = {'.txt', '.md', '.csv', '.json', '.jsonl', '.docx', '.xlsx', '.pdf',
             '.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff'}
SLOTS = threading.BoundedSemaphore(2)


class ExtractionError(ValueError):
    pass


class TextCollector:
    def __init__(self):
        self.text = ''
        self.pages = []
        self.truncated = False
        self.warnings = []

    def add(self, text, page=1, kind='text', label='', tool='', confidence=None):
        text = str(text).replace('\x00', '').strip()
        separator = '\n\n' if self.text and text else ''
        remaining = MAX_TEXT_CHARS - len(self.text) - len(separator)
        if remaining <= 0:
            self.truncated = True
            return False
        if len(text) > remaining:
            text = text[:remaining]
            self.truncated = True
        start = len(self.text) + len(separator)
        self.text += separator + text
        self.pages.append(dict(page=page, kind=kind, label=label, start=start,
                               end=len(self.text), tool=tool, confidence=confidence))
        return not self.truncated

    def result(self, tool):
        status = 'partial' if self.truncated or self.warnings else 'completed'
        if not self.text and self.warnings:
            status = 'failed'
        return dict(status=status, text=self.text, pages=self.pages, tool=tool,
                    error='；'.join(self.warnings)[:1000], truncated=self.truncated)


def _decode(raw):
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        return raw.decode('utf-16')
    for encoding in ('utf-8-sig', 'gb18030'):
        try:
            text = raw.decode(encoding)
            if '\x00' in text:
                raise ExtractionError('该文件包含二进制数据，无法作为文本读取')
            return text
        except UnicodeDecodeError:
            continue
    raise ExtractionError('无法识别文本编码，请另存为 UTF-8 后重新上传')


def _archive(path):
    archive = zipfile.ZipFile(path)
    try:
        items = archive.infolist()
        if len(items) > 5000 or sum(item.file_size for item in items) > MAX_ARCHIVE_BYTES:
            raise ExtractionError('Office 文档展开后过大，已停止解析')
        names = set()
        for item in items:
            value = item.orig_filename
            name = PurePosixPath(value)
            if (name.is_absolute() or '..' in name.parts or '\\' in value or ':' in value
                    or item.flag_bits & 1 or value.casefold() in names):
                raise ExtractionError('Office 文档包含不安全或重复路径')
            names.add(value.casefold())
        return archive
    except BaseException:
        archive.close()
        raise


def _xml(archive, name):
    info = archive.getinfo(name)
    if info.file_size > MAX_XML_BYTES:
        raise ExtractionError('单个 Office 文本片段超过 20 MB，已停止解析')
    raw = archive.read(name)
    markup = raw.upper().replace(b'\x00', b'')
    if b'<!DOCTYPE' in markup or b'<!ENTITY' in markup:
        raise ExtractionError('Office 文档包含禁止的 XML 实体声明')
    return ET.fromstring(raw)


def _docx(path, out):
    ns = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
    with _archive(path) as archive:
        root = _xml(archive, 'word/document.xml')
        fragments, length = [], 0
        for element in root.iter():
            value = (element.text or '') if element.tag == ns + 't' else '\t' if element.tag == ns + 'tab' else '\n' if element.tag in (ns + 'br', ns + 'cr') else ''
            if value:
                fragments.append(value)
                length += len(value)
            if element.tag == ns + 'p' and fragments:
                fragments.append('\n')
                length += 1
            if length >= MAX_TEXT_CHARS:
                out.truncated = True
                break
        # Word pagination depends on fonts and layout; never invent physical page numbers.
        out.add(''.join(fragments), kind='document', label='Word 文本（未渲染页码）', tool='office-xml')


def _xlsx(path, out):
    ns = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
    relns = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
    with _archive(path) as archive:
        shared = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            strings = _xml(archive, 'xl/sharedStrings.xml')
            shared_chars = 0
            for item in strings.findall(ns + 'si'):
                value = ''.join(element.text or '' for element in item.iter(ns + 't'))
                shared_chars += len(value)
                if len(shared) >= MAX_CELLS or shared_chars > MAX_TEXT_CHARS:
                    raise ExtractionError('Excel 共享文本超出解析上限')
                shared.append(value)
        workbook = _xml(archive, 'xl/workbook.xml')
        relationships = _xml(archive, 'xl/_rels/workbook.xml.rels')
        targets = {rel.get('Id'): rel.get('Target', '') for rel in relationships
                   if rel.get('TargetMode') != 'External'}
        cells = 0
        for number, sheet in enumerate(workbook.findall(ns + 'sheets/' + ns + 'sheet'), 1):
            if number > MAX_PAGES:
                out.truncated = True
                break
            target = targets.get(sheet.get(relns + 'id'), '')
            name = target.lstrip('/') if target.startswith('/') else 'xl/' + target
            if '..' in PurePosixPath(name).parts or not name.startswith('xl/worksheets/'):
                raise ExtractionError('Excel 工作表引用路径无效')
            worksheet = _xml(archive, name)
            lines, length = [], 0
            for row in worksheet.iter(ns + 'row'):
                values = []
                for cell in row.findall(ns + 'c'):
                    cells += 1
                    if cells > MAX_CELLS:
                        out.truncated = True
                        break
                    value = cell.findtext(ns + 'v', '')
                    if cell.get('t') == 's':
                        index = int(value)
                        if index < 0 or index >= len(shared):
                            raise ExtractionError('Excel 共享文本引用无效')
                        value = shared[index]
                    elif cell.get('t') == 'inlineStr':
                        value = ''.join(item.text or '' for item in cell.iter(ns + 't'))
                    elif not value and cell.find(ns + 'f') is not None:
                        value = '=' + (cell.findtext(ns + 'f') or '')
                    values.append((cell.get('r') or '') + ': ' + value)
                line = '\t'.join(values)
                length += len(line)
                if length > MAX_TEXT_CHARS - len(out.text):
                    out.truncated = True
                    break
                lines.append(line)
                if out.truncated:
                    break
            out.add('\n'.join(lines), number, 'sheet', sheet.get('name') or str(number), 'office-xml')
            if out.truncated:
                break


def _ocr_engine():
    try:
        import rapidocr
    except ImportError as exc:
        raise ExtractionError('本地 OCR 组件未安装，请运行安装脚本后重试') from exc
    model_root = Path(rapidocr.__file__).resolve().parent / 'models'
    params = {'Global.log_level': 'critical', 'Global.max_side_len': 2000,
              'EngineConfig.onnxruntime.intra_op_num_threads': 1,
              'EngineConfig.onnxruntime.inter_op_num_threads': 1,
              'EngineConfig.onnxruntime.use_cuda': False,
              'EngineConfig.onnxruntime.use_dml': False}
    for task, name in (('Det', 'det'), ('Cls', 'cls'), ('Rec', 'rec')):
        models = sorted(model_root.glob('*' + name + '*.onnx'))
        if not models:
            raise ExtractionError('本地 OCR 模型缺失；不会自动联网下载，请重新安装 OCR 组件')
        params[task + '.model_path'] = str(models[0])
    return rapidocr.RapidOCR(params=params)


def _ocr_image(image, engine):
    import numpy as np
    if image.width * image.height > MAX_PIXELS:
        raise ExtractionError('图片像素超过 2500 万，已停止识别')
    image = image.convert('RGB')
    image.thumbnail((2000, 2000))
    result = engine(np.asarray(image))
    texts = list(result.txts) if result.txts is not None else []
    scores = [float(value) for value in result.scores if math.isfinite(float(value))] if result.scores is not None else []
    confidence = min(1.0, max(0.0, sum(scores) / len(scores))) if scores else None
    return '\n'.join(texts), confidence


def _image(path, out):
    from PIL import Image, ImageOps
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    engine = _ocr_engine()
    with Image.open(path) as image:
        if image.width * image.height > MAX_PIXELS:
            raise ExtractionError('图片像素超过 2500 万，已停止识别')
        frames = getattr(image, 'n_frames', 1)
        for index in range(min(frames, MAX_OCR_PAGES)):
            image.seek(index)
            if image.width * image.height > MAX_PIXELS:
                raise ExtractionError('图片像素超过 2500 万，已停止识别')
            oriented = ImageOps.exif_transpose(image)
            text, confidence = _ocr_image(oriented, engine)
            out.add(text, index + 1, 'image', '图片' if frames == 1 else '图像页', 'rapidocr-local', confidence)
            if out.truncated:
                break
        if frames > MAX_OCR_PAGES:
            out.truncated = True


def _pdf(path, out):
    import pypdfium2 as pdfium
    engine, ocr_pages = None, 0
    with closing(pdfium.PdfDocument(str(path))) as document:
        if len(document) > MAX_PAGES:
            out.truncated = True
        for index in range(min(len(document), MAX_PAGES)):
            with closing(document[index]) as page:
                with closing(page.get_textpage()) as textpage:
                    count = textpage.count_chars()
                    text = textpage.get_text_range(0, min(count, MAX_TEXT_CHARS - len(out.text)))
                    if count > MAX_TEXT_CHARS - len(out.text):
                        out.truncated = True
                tool, confidence = 'pdfium-text', None
                if not text.strip():
                    if ocr_pages >= MAX_OCR_PAGES:
                        out.truncated = True
                        out.warnings.append('扫描页 OCR 最多识别 30 页')
                        break
                    width, height = page.get_size()
                    if not all(math.isfinite(value) and value > 0 for value in (width, height)):
                        raise ExtractionError('PDF 页面尺寸无效')
                    scale = min(2.0, 2000 / max(width, height))
                    try:
                        engine = engine or _ocr_engine()
                        with closing(page.render(scale=scale)) as bitmap:
                            text, confidence = _ocr_image(bitmap.to_pil(), engine)
                        tool = 'pdfium+rapidocr-local'
                        ocr_pages += 1
                    except ExtractionError as exc:
                        out.warnings.append(str(exc))
                out.add(text, index + 1, 'page', 'PDF 第 ' + str(index + 1) + ' 页', tool, confidence)
                if len(out.text) >= MAX_TEXT_CHARS:
                    out.truncated = True
                    break


def parse_file(path, extension):
    path = Path(path)
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ExtractionError('附件超过 50 MB 文本提取上限，原文件仍可下载')
    out = TextCollector()
    if extension in ('.txt', '.md', '.csv', '.json', '.jsonl'):
        raw = path.read_bytes()
        text = _decode(raw)
        if extension == '.json':
            text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
        out.add(text, kind='text', label='文本文件', tool='text-decoder')
        return out.result('text-decoder')
    if extension == '.docx':
        _docx(path, out)
        return out.result('office-xml')
    if extension == '.xlsx':
        _xlsx(path, out)
        return out.result('office-xml')
    if extension == '.pdf':
        _pdf(path, out)
        return out.result('pdfium/rapidocr-local')
    if extension in SUPPORTED:
        _image(path, out)
        return out.result('rapidocr-local')
    return dict(status='unsupported', text='', pages=[], tool='', error='暂不支持该文件格式的文字提取，原文件仍可下载', truncated=False)


def _deny_network(*args, **kwargs):
    raise ExtractionError('附件文字提取仅在本机运行，网络访问已禁止')


def _limit_worker_memory():
    """Bound parser/OCR memory; native faults stay inside this one worker."""
    if os.name != 'nt':
        try:
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
        except (ImportError, OSError, ValueError):
            pass
        return
    # Keep the job handle alive through worker exit. Windows job objects enforce
    # commit memory even for native PDFium/ONNX allocation without another package.
    import ctypes
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [('PerProcessUserTimeLimit', ctypes.c_longlong),
                    ('PerJobUserTimeLimit', ctypes.c_longlong), ('LimitFlags', wintypes.DWORD),
                    ('MinimumWorkingSetSize', ctypes.c_size_t), ('MaximumWorkingSetSize', ctypes.c_size_t),
                    ('ActiveProcessLimit', wintypes.DWORD), ('Affinity', ctypes.c_size_t),
                    ('PriorityClass', wintypes.DWORD), ('SchedulingClass', wintypes.DWORD)]

    class IOCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in
                    ('ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
                     'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [('BasicLimitInformation', BasicLimits), ('IoInfo', IOCounters),
                    ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                    ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    kernel.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel.CreateJobObjectW(None, None)
    if handle:
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x100  # JOB_OBJECT_LIMIT_PROCESS_MEMORY
        limits.ProcessMemoryLimit = 1024**3
        if (kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits))
                and kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess())):
            globals()['_MEMORY_JOB_HANDLE'] = handle
        else:
            kernel.CloseHandle(handle)


def _worker(path, extension, output):
    # Parsing never accepts URLs and never downloads models or uploads attachments.
    socket.socket.connect = _deny_network
    socket.socket.connect_ex = _deny_network
    socket.create_connection = _deny_network
    os.environ['OMP_NUM_THREADS'] = '1'
    try:
        _limit_worker_memory()
        result = parse_file(path, extension)
    except ExtractionError as exc:
        result = dict(status='failed', text='', pages=[], tool='', error=str(exc)[:500], truncated=False)
    except Exception as exc:
        result = dict(status='failed', text='', pages=[], tool='', error='解析失败（' + type(exc).__name__ + '），原附件未改变', truncated=False)
    Path(output).write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')


def _run_isolated(path, extension, stopped=None):
    from app.config import ROOT
    output = path.with_name('result.json')
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    process = subprocess.Popen([sys.executable, '-m', 'app.services.extraction', str(path), extension, str(output)],
                               cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while process.poll() is None:
        if stopped is not None and stopped():
            process.kill()
            process.wait(timeout=5)
            raise InterruptedError('Attachment extraction stopped')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            process.kill()
            process.wait(timeout=5)
            return dict(status='timeout', text='', pages=[], tool='', error='解析超过 90 秒，已停止；原附件仍可下载', truncated=False)
        try:
            process.wait(timeout=min(0.2, remaining))
        except subprocess.TimeoutExpired:
            continue
    if process.returncode != 0 or not output.is_file() or output.stat().st_size > 20 * 1024**2:
        return dict(status='failed', text='', pages=[], tool='', error='本地解析程序异常退出，原附件未改变', truncated=False)
    return json.loads(output.read_text(encoding='utf-8'))


def _serialize(row):
    return {column.name: getattr(row, column.name) for column in row.__table__.columns if column.name != 'job_token'}


def _checksum_with_stop(path, stopped=None):
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024**2), b''):
            if stopped is not None and stopped():
                raise InterruptedError('Attachment extraction stopped')
            checksum.update(chunk)
    return checksum.hexdigest()


def _notify_completed(entry_uuid):
    # Queue follow-up work only after commit; a queue failure must not discard text.
    try:
        from app.semantic_routes import enqueue_index
        enqueue_index(entry_uuid)
    except Exception as exc:
        logging.getLogger('personal_database').warning('Attachment text committed; semantic enqueue failed (%s)', type(exc).__name__)
    try:
        from app.services.ai_worker import record_changed
        record_changed()
    except ImportError:
        pass  # The service also works independently of optional AI workflow modules.
    except Exception as exc:
        logging.getLogger('personal_database').warning('Attachment text committed; AI notification failed (%s)', type(exc).__name__)


def get_extraction(uid):
    from fastapi import HTTPException
    from sqlalchemy import select
    from app.database import transaction
    from app.models import Attachment
    from app.extraction_models import AttachmentExtraction
    with transaction() as session:
        attachment = session.scalar(select(Attachment).where(Attachment.uuid == uid))
        if attachment is None:
            raise HTTPException(404, '附件不存在')
        row = session.get(AttachmentExtraction, uid)
        if row is None:
            return dict(attachment_uuid=uid, source_sha256=attachment.sha256, status='pending',
                        text='', pages=[], tool='', error='', truncated=False, parser_version=PARSER_VERSION)
        result = _serialize(row)
        if row.source_sha256 != attachment.sha256:
            result.update(status='stale', text='', pages=[], error='原附件已经变化，请重新提取')
        elif row.status == 'running' and (datetime.now(timezone.utc) - datetime.fromisoformat(row.updated_at)).total_seconds() > TIMEOUT_SECONDS + 30:
            result.update(status='failed', text='', pages=[], error='上一次提取已中断，请重新提取')
        return result


def extract_attachment(uid, force=False, stopped=None):
    from fastapi import HTTPException
    from sqlalchemy import select
    from app.config import HOME
    from app.database import transaction
    from app.models import Attachment, DailyEntry, now
    from app.extraction_models import AttachmentExtraction
    from app.services.backup import safe_attachment
    if stopped is not None and stopped():
        raise InterruptedError('Attachment extraction stopped')
    if not SLOTS.acquire(blocking=False):
        raise HTTPException(429, '已有附件正在识别，请稍后重试')
    token = str(uuid.uuid4())
    try:
        with transaction() as session:
            if stopped is not None and stopped():
                raise InterruptedError('Attachment extraction stopped')
            attachment = session.scalar(select(Attachment).where(Attachment.uuid == uid))
            if attachment is None:
                raise HTTPException(404, '附件不存在')
            entry = session.get(DailyEntry, attachment.entry_id)
            if entry is None or entry.deleted_at:
                raise HTTPException(409, '回收站中的附件不能开始文字提取')
            entry_uuid = entry.uuid
            source_sha, relative, filename = attachment.sha256, attachment.file_path, attachment.original_filename
            row = session.get(AttachmentExtraction, uid)
            if row and row.status == 'running':
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(row.updated_at)).total_seconds()
                if age < TIMEOUT_SECONDS + 30:
                    raise HTTPException(409, '该附件正在提取，请稍后查看结果')
            if row and not force and row.source_sha256 == source_sha and row.parser_version == PARSER_VERSION and row.status in ('completed', 'partial', 'unsupported'):
                return _serialize(row)
            if row is None:
                row = AttachmentExtraction(attachment_uuid=uid, source_sha256=source_sha)
                session.add(row)
            row.source_sha256, row.status, row.job_token = source_sha, 'running', token
            row.text, row.pages, row.error = '', [], ''
            row.tool, row.truncated, row.parser_version = '', False, PARSER_VERSION
            row.updated_at = now()
        result = None
        try:
            source = safe_attachment(relative)
            if not source.is_file():
                raise ExtractionError('附件原文件缺失，无法提取文字')
            if source.stat().st_size > MAX_FILE_BYTES:
                raise ExtractionError('附件超过 50 MB 文本提取上限，原文件仍可下载')
            with tempfile.TemporaryDirectory(prefix='pld-extract-', dir=HOME) as temporary:
                copy = Path(temporary) / 'source'
                copied, checksum = 0, hashlib.sha256()
                with source.open('rb') as inp, copy.open('wb') as out:
                    for chunk in iter(lambda: inp.read(1024**2), b''):
                        if stopped is not None and stopped():
                            raise InterruptedError('Attachment extraction stopped')
                        copied += len(chunk)
                        if copied > MAX_FILE_BYTES:
                            raise ExtractionError('附件超过 50 MB 文本提取上限')
                        checksum.update(chunk)
                        out.write(chunk)
                if checksum.hexdigest() != source_sha:
                    raise ExtractionError('附件原文件与保存的 SHA256 不符，已停止提取')
                result = (_run_isolated(copy, Path(filename).suffix.lower(), stopped=stopped) if stopped is not None
                          else _run_isolated(copy, Path(filename).suffix.lower()))
                if not source.is_file() or _checksum_with_stop(source, stopped) != source_sha:
                    raise ExtractionError('提取期间原附件发生变化，派生文字已丢弃')
        except ExtractionError as exc:
            result = dict(status='failed', text='', pages=[], tool='', error=str(exc), truncated=False)
        except Exception as exc:
            result = dict(status='failed', text='', pages=[], tool='', error='本地提取未完成（' + type(exc).__name__ + '），原附件未改变', truncated=False)
        if stopped is not None and stopped():
            raise InterruptedError('Attachment extraction stopped')
        with transaction() as session:
            if stopped is not None and stopped():
                raise InterruptedError('Attachment extraction stopped')
            attachment = session.scalar(select(Attachment).where(Attachment.uuid == uid))
            row = session.get(AttachmentExtraction, uid)
            if attachment is None or row is None:
                raise HTTPException(404, '提取期间附件已删除，结果已丢弃')
            if attachment.sha256 != source_sha or attachment.file_path != relative or row.job_token != token:
                raise HTTPException(409, '附件或提取任务已经变化，旧结果已丢弃')
            for key in ('status', 'text', 'pages', 'tool', 'error', 'truncated'):
                setattr(row, key, result[key])
            row.updated_at = now()
            session.flush()
            serialized = _serialize(row)
        if serialized['status'] in ('completed', 'partial') and not (stopped is not None and stopped()):
            _notify_completed(entry_uuid)
        return serialized
    finally:
        SLOTS.release()


if __name__ == '__main__':
    if len(sys.argv) != 4:
        raise SystemExit(2)
    _worker(sys.argv[1], sys.argv[2], sys.argv[3])
