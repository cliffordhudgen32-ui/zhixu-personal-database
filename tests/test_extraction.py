import hashlib
import io
import json
import sqlite3
import threading
import zipfile
from contextlib import closing
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.database import LOCK, transaction
from app.extraction_models import AttachmentExtraction
from app.models import Attachment
from app.services import extraction


def upload(client, create, name, body):
    entry = create(title='附件识别测试')
    result = client.post('/api/entries/' + entry['uuid'] + '/attachments', files={'file': (name, body)})
    assert result.status_code == 200, result.text
    return entry, result.json()


@pytest.mark.parametrize('name,body,expected', [
    ('中文.txt', '中文工作记录：今天检查参数。'.encode(), '中文工作记录'),
    ('旧编码.txt', '学习记录和项目进度'.encode('gb18030'), '学习记录'),
    ('utf16.txt', '中文 UTF16 文档'.encode('utf-16'), '中文 UTF16'),
    ('note.md', '# 今日记录\n完成资料整理。'.encode(), '# 今日记录'),
    ('sheet.csv', '日期,事项\n2026-10-03,整理附件'.encode(), '整理附件'),
    ('data.json', json.dumps({'记录': '今天学习数据库'}, ensure_ascii=False).encode(), '今天学习数据库'),
])
def test_plain_formats_preserve_original(client, create, name, body, expected):
    _, attachment = upload(client, create, name, body)
    assert extraction.get_extraction(attachment['uuid'])['status'] == 'pending'
    result = extraction.extract_attachment(attachment['uuid'])
    assert result['status'] == 'completed', result
    assert expected in result['text']
    assert result['source_sha256'] == hashlib.sha256(body).hexdigest()
    page = result['pages'][0]
    assert result['text'][page['start']:page['end']]
    assert client.get('/api/attachments/' + attachment['uuid']).content == body
    assert extraction.extract_attachment(attachment['uuid'])['updated_at'] == result['updated_at']


def office_archive(files):
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return result.getvalue()


def test_docx_and_xlsx_structured_text(client, create):
    body = office_archive({'word/document.xml': '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>中文项目报告</w:t></w:r></w:p><w:p><w:r><w:t>本周完成整理</w:t></w:r></w:p></w:body></w:document>'})
    _, attachment = upload(client, create, '报告.docx', body)
    result = extraction.extract_attachment(attachment['uuid'])
    assert result['status'] == 'completed' and '中文项目报告' in result['text'], result
    assert result['pages'][0]['kind'] == 'document'
    assert '未渲染页码' in result['pages'][0]['label']
    body = office_archive({
        'xl/workbook.xml': '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="学习记录" sheetId="1" r:id="rId1"/></sheets></workbook>',
        'xl/_rels/workbook.xml.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
        'xl/sharedStrings.xml': '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>中文资料</t></si></sst>',
        'xl/worksheets/sheet1.xml': '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="inlineStr"><is><t>已完成</t></is></c><c r="C1"><f>1+2</f></c></row></sheetData></worksheet>',
    })
    _, attachment = upload(client, create, '资料.xlsx', body)
    result = extraction.extract_attachment(attachment['uuid'])
    assert result['status'] == 'completed', result
    assert 'A1: 中文资料' in result['text'] and 'B1: 已完成' in result['text'] and '=1+2' in result['text']
    assert result['pages'][0]['label'] == '学习记录' and result['pages'][0]['kind'] == 'sheet'


def test_unsupported_and_invalid_input_dont_break_download(client, create):
    for name, body, status in [('unknown.bin', b'\x00\x01', 'unsupported'), ('invalid.json', b'not-json', 'failed'), ('bad.docx', b'not-a-zip', 'failed')]:
        _, attachment = upload(client, create, name, body)
        result = extraction.extract_attachment(attachment['uuid'])
        assert result['status'] == status and not result['text'], result
        assert client.get('/api/attachments/' + attachment['uuid']).content == body


@pytest.mark.parametrize('encoding', ['utf-8', 'utf-16'])
def test_office_xml_entities_paths_and_expansion_rejected(tmp_path, monkeypatch, encoding):
    malicious = tmp_path / 'xml.docx'
    malicious.write_bytes(office_archive({'word/document.xml': '<!DOCTYPE a [<!ENTITY x "secret">]><a>&x;</a>'.encode(encoding)}))
    with pytest.raises(extraction.ExtractionError, match='XML'):
        extraction.parse_file(malicious, '.docx')
    malicious.write_bytes(office_archive({'../escaped': b'x', 'word/document.xml': '<a/>'}))
    with pytest.raises(extraction.ExtractionError, match='路径'):
        extraction.parse_file(malicious, '.docx')
    malicious.write_bytes(office_archive({'word/document.xml': b'x' * 1000}))
    monkeypatch.setattr(extraction, 'MAX_ARCHIVE_BYTES', 100)
    with pytest.raises(extraction.ExtractionError, match='展开后过大'):
        extraction.parse_file(malicious, '.docx')
    assert not (tmp_path.parent / 'escaped').exists()


def test_text_character_limit_and_timeout(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    source.write_text('中文' * 100, encoding='utf-8')
    monkeypatch.setattr(extraction, 'MAX_TEXT_CHARS', 40)
    result = extraction.parse_file(source, '.txt')
    assert result['status'] == 'partial' and result['truncated'] and len(result['text']) == 40
    monkeypatch.setattr(extraction, 'TIMEOUT_SECONDS', 0.01)
    result = extraction._run_isolated(source, '.txt')
    assert result['status'] == 'timeout'
    assert source.read_text(encoding='utf-8') == '中文' * 100


def fake_result():
    return dict(status='completed', text='derived-text', pages=[dict(page=1, kind='text', label='测试', start=0, end=12, tool='test', confidence=None)], tool='test', error='', truncated=False)


def test_extraction_work_releases_database_lock_and_deletion_discards_result(client, create, monkeypatch):
    _, attachment = upload(client, create, 'delete.txt', b'original')
    entered, release = threading.Event(), threading.Event()
    outcomes = []

    def wait_worker(path, extension):
        entered.set()
        assert release.wait(10)
        return fake_result()

    def extract_thread():
        try:
            outcomes.append(extraction.extract_attachment(attachment['uuid']))
        except Exception as exc:
            outcomes.append(exc)

    monkeypatch.setattr(extraction, '_run_isolated', wait_worker)
    thread = threading.Thread(target=extract_thread)
    thread.start()
    assert entered.wait(5)
    try:
        acquired = LOCK.acquire(timeout=0.2)
        if acquired:
            LOCK.release()
        assert acquired, 'CPU parsing held the global database lock'
        assert extraction.get_extraction(attachment['uuid'])['status'] == 'running'
        assert client.delete('/api/attachments/' + attachment['uuid']).status_code == 200
    finally:
        release.set()
        thread.join(10)
    assert len(outcomes) == 1 and isinstance(outcomes[0], HTTPException) and outcomes[0].status_code == 404
    with transaction() as session:
        assert session.get(AttachmentExtraction, attachment['uuid']) is None


def test_original_changes_discard_derived_text(client, create, monkeypatch):
    _, attachment = upload(client, create, 'changed.txt', b'original')
    from app.services.backup import safe_attachment
    path = safe_attachment(attachment['file_path'])

    def mutate_original(copy, extension):
        path.write_bytes(b'externally-changed')
        return fake_result()

    monkeypatch.setattr(extraction, '_run_isolated', mutate_original)
    try:
        result = extraction.extract_attachment(attachment['uuid'])
        assert result['status'] == 'failed' and not result['text']
        assert '发生变化' in result['error']
    finally:
        path.write_bytes(b'original')


def test_metadata_changes_discard_old_result(client, create, monkeypatch):
    _, attachment = upload(client, create, 'metadata.txt', b'original')
    original_sha = attachment['sha256']

    def mutate_metadata(copy, extension):
        with transaction() as session:
            row = session.scalar(select(Attachment).where(Attachment.uuid == attachment['uuid']))
            row.sha256 = '0' * 64
        return fake_result()

    monkeypatch.setattr(extraction, '_run_isolated', mutate_metadata)
    try:
        with pytest.raises(HTTPException) as error:
            extraction.extract_attachment(attachment['uuid'])
        assert error.value.status_code == 409
        assert not extraction.get_extraction(attachment['uuid'])['text']
    finally:
        with transaction() as session:
            session.scalar(select(Attachment).where(Attachment.uuid == attachment['uuid'])).sha256 = original_sha


def test_duplicate_work_and_capacity_limits(client, create, monkeypatch):
    _, attachment = upload(client, create, 'busy.txt', b'original')
    from app.models import now
    with transaction() as session:
        session.add(AttachmentExtraction(attachment_uuid=attachment['uuid'], source_sha256=attachment['sha256'],
                                         status='running', job_token='existing', updated_at=now()))
    with pytest.raises(HTTPException) as error:
        extraction.extract_attachment(attachment['uuid'])
    assert error.value.status_code == 409
    slots = threading.BoundedSemaphore(1)
    slots.acquire()
    monkeypatch.setattr(extraction, 'SLOTS', slots)
    with pytest.raises(HTTPException) as error:
        extraction.extract_attachment(attachment['uuid'])
    assert error.value.status_code == 429
    slots.release()


@pytest.mark.parametrize('status', ['completed', 'partial'])
def test_followup_notification_happens_after_commit(client, create, monkeypatch, status):
    entry, attachment = upload(client, create, 'notify.txt', b'original')
    notifications = []

    def worker(copy, extension):
        result = fake_result()
        result.update(status=status, truncated=status == 'partial')
        return result

    def notified(entry_uuid):
        from app.services.backup import db_path
        with closing(sqlite3.connect(db_path())) as conn:
            # A separate SQLite connection sees completion only after commit.
            persisted = conn.execute('SELECT status FROM attachment_extractions WHERE attachment_uuid=?', (attachment['uuid'],)).fetchone()[0]
        notifications.append((entry_uuid, persisted))

    monkeypatch.setattr(extraction, '_run_isolated', worker)
    monkeypatch.setattr(extraction, '_notify_completed', notified)
    assert extraction.extract_attachment(attachment['uuid'])['status'] == status
    assert notifications == [(entry['uuid'], status)]


def test_stopped_extraction_does_not_commit_or_notify(client, create, monkeypatch):
    _, attachment = upload(client, create, 'stop.txt', b'original')
    stopped = threading.Event()
    notifications = []

    def worker(path, extension, **options):
        stopped.set()
        return fake_result()

    monkeypatch.setattr(extraction, '_run_isolated', worker)
    monkeypatch.setattr(extraction, '_notify_completed', lambda uid: notifications.append(uid))
    with pytest.raises(InterruptedError):
        extraction.extract_attachment(attachment['uuid'], stopped=stopped.is_set)
    with transaction() as session:
        row = session.get(AttachmentExtraction, attachment['uuid'])
        assert row.status == 'running' and not row.text
    assert not notifications
    assert client.get('/api/attachments/' + attachment['uuid']).content == b'original'


def test_stop_kills_isolated_parser(tmp_path):
    source = tmp_path / 'source'
    source.write_bytes(b'original')
    with pytest.raises(InterruptedError):
        extraction._run_isolated(source, '.txt', stopped=lambda: True)
    assert source.read_bytes() == b'original'


def test_extraction_api_and_session_token(client, create):
    _, attachment = upload(client, create, 'api.txt', '接口文本'.encode())
    url = '/api/attachments/' + attachment['uuid'] + '/extraction'
    assert client.get(url).status_code == 200
    assert client.post(url, headers={'x-local-token': ''}).status_code == 403
    result = client.post(url)
    assert result.status_code == 200 and result.json()['status'] == 'completed', result.text


def chinese_image():
    from PIL import Image, ImageDraw, ImageFont
    image = Image.new('RGB', (1100, 300), 'white')
    font_path = Path('C:/Windows/Fonts/msyh.ttc')
    if not font_path.is_file():
        pytest.skip('Chinese font unavailable for the OCR fixture')
    font = ImageFont.truetype(str(font_path), 52)
    ImageDraw.Draw(image).text((50, 80), '今日工作记录  项目学习完成', fill='black', font=font)
    return image


def test_real_local_chinese_image_ocr(client, create):
    pytest.importorskip('rapidocr')
    image = chinese_image()
    output = io.BytesIO()
    image.save(output, format='PNG')
    _, attachment = upload(client, create, '中文识别.png', output.getvalue())
    result = extraction.extract_attachment(attachment['uuid'])
    assert result['status'] == 'completed', result
    assert '工作记录' in result['text'] and '完成' in result['text'], result
    assert result['pages'][0]['tool'] == 'rapidocr-local'
    assert client.get('/api/attachments/' + attachment['uuid']).content == output.getvalue()


def test_real_scanned_pdf_local_ocr(client, create):
    pytest.importorskip('rapidocr')
    pytest.importorskip('pypdfium2')
    image = chinese_image()
    output = io.BytesIO()
    image.save(output, format='PDF', resolution=100)
    _, attachment = upload(client, create, '扫描文档.pdf', output.getvalue())
    result = extraction.extract_attachment(attachment['uuid'])
    assert result['status'] == 'completed', result
    assert '工作记录' in result['text'], result
    assert result['pages'][0]['page'] == 1 and result['pages'][0]['tool'] == 'pdfium+rapidocr-local'


def test_pdf_text_layer_extraction(client, create):
    pytest.importorskip('pypdfium2')
    # Minimal hand-written text-layer PDF; offsets generated from actual bytes.
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    stream = b'BT /F1 24 Tf 30 750 Td (PDF text layer evidence) Tj ET'
    objects.append(b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream')
    raw, offsets = bytearray(b'%PDF-1.4\n'), [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(raw))
        raw += str(number).encode() + b' 0 obj\n' + obj + b'\nendobj\n'
    xref = len(raw)
    raw += b'xref\n0 6\n0000000000 65535 f \n'
    for offset in offsets[1:]:
        raw += f'{offset:010d} 00000 n \n'.encode()
    raw += b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n' + str(xref).encode() + b'\n%%EOF'
    _, attachment = upload(client, create, '文字层.pdf', bytes(raw))
    result = extraction.extract_attachment(attachment['uuid'])
    assert result['status'] == 'completed' and 'PDF text layer evidence' in result['text'], result
    assert result['pages'][0]['tool'] == 'pdfium-text'
