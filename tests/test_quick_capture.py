"""Capture retry tests use only the test database and temporary user-selected files."""
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import desktop_control as desktop
from scripts import quick_capture as capture


@pytest.fixture
def capture_manager(tmp_path, client, monkeypatch):
    manager = SimpleNamespace(config=desktop.load_config(tmp_path, {'APP_PORT': '8123'}),
                              start=Mock(return_value={'token': client.headers['x-local-token']}))

    def api(config, endpoint, *, method='GET', payload=None, token=None, **kwargs):
        response = client.request(method, endpoint, json=payload,
                                  headers={'x-local-token': token} if token else None)
        if response.status_code >= 300:
            raise desktop.ControlError(response.json().get('detail', '请求未完成'),
                                       status_code=response.status_code)
        return response.json()

    monkeypatch.setattr(capture, 'request_json', api)
    return manager, api


def test_uncertain_record_commit_recovers_same_uuid_and_preserves_raw_text(capture_manager, client, monkeypatch):
    manager, api = capture_manager
    calls = []

    def interrupted(config, endpoint, **kwargs):
        result = api(config, endpoint, **kwargs)
        calls.append((kwargs.get('method', 'GET'), endpoint))
        if endpoint == '/api/entries':
            raise desktop.ControlError('模拟提交成功后连接中断')
        return result

    monkeypatch.setattr(capture, 'request_json', interrupted)
    original = '  上午巡查发现异常。\n\n保留换行与措辞。  '
    draft = capture.CaptureDraft(original)
    assert draft.submit(manager)['uuid'] == draft.uuid
    assert draft.submit(manager)['uuid'] == draft.uuid
    saved = client.get('/api/entries/' + draft.uuid).json()
    assert saved['content'] == saved['original_content'] == original
    assert saved['item_type'] == 'inbox'
    assert saved['is_archived'] is False
    assert saved['generated_by_ai'] is False
    assert sum(method == 'POST' for method, endpoint in calls) == 1


def test_unconfirmed_response_retries_permanent_uuid_without_duplicate(capture_manager, client, monkeypatch):
    manager, api = capture_manager
    disconnected = {'active': True}

    def interrupted(config, endpoint, **kwargs):
        if disconnected['active']:
            if kwargs.get('method') == 'POST':
                api(config, endpoint, **kwargs)
            raise desktop.ControlError('模拟提交后断网')
        return api(config, endpoint, **kwargs)

    monkeypatch.setattr(capture, 'request_json', interrupted)
    draft = capture.CaptureDraft('模拟未确认提交，重试不能生成第二条记录。')
    with pytest.raises(desktop.ControlError):
        draft.submit(manager)
    assert not draft.record_saved and draft.write_attempted
    disconnected['active'] = False
    draft.submit(manager)
    records = client.get('/api/entries', params={'q': draft.content, 'item_type': 'inbox'}).json()
    assert [item['uuid'] for item in records['items']] == [draft.uuid]


def test_partial_attachments_retry_only_missing_files(capture_manager, client, tmp_path, monkeypatch):
    manager, _ = capture_manager
    first, second = tmp_path / '第一份.txt', tmp_path / '第二份.txt'
    first.write_text('原始附件一', encoding='utf-8')
    second.write_text('原始附件二', encoding='utf-8')
    attempted = []
    fail_second = {'active': True}

    def upload(config, uid, item, token):
        attempted.append(item.name)
        if item.path == second and fail_second['active']:
            raise desktop.ControlError('模拟第二个附件失败')
        response = client.post(f'/api/entries/{uid}/attachments',
                               files={'file': (item.name, item.path.read_bytes(), 'text/plain')})
        assert response.status_code == 200, response.text
        return response.json()

    monkeypatch.setattr(capture, 'upload_file', upload)
    draft = capture.CaptureDraft('原文先保存，两个附件分步补充。', (first, second))
    with pytest.raises(desktop.ControlError, match='第二个附件失败'):
        draft.submit(manager)
    assert draft.record_saved and draft.uploaded == {str(first.resolve())}
    fail_second['active'] = False
    draft.submit(manager)
    saved = client.get('/api/entries/' + draft.uuid).json()
    assert len(saved['attachments']) == 2
    assert attempted == [first.name, second.name, second.name]
    assert saved['content'] == draft.content


def test_committed_attachment_with_lost_response_is_reconciled(capture_manager, client, tmp_path, monkeypatch):
    manager, _ = capture_manager
    path = tmp_path / '截图说明.txt'
    path.write_text('模拟附件已提交但响应丢失', encoding='utf-8')
    uploads = []

    def lost_response(config, uid, item, token):
        response = client.post(f'/api/entries/{uid}/attachments',
                               files={'file': (item.name, item.path.read_bytes(), 'text/plain')})
        assert response.status_code == 200
        uploads.append(item.name)
        raise desktop.ControlError('模拟附件响应丢失')

    monkeypatch.setattr(capture, 'upload_file', lost_response)
    draft = capture.CaptureDraft('附件已保存时核对摘要，不再重复上传。', (path,))
    draft.submit(manager)
    draft.submit(manager)
    assert uploads == [path.name]
    assert len(client.get('/api/entries/' + draft.uuid).json()['attachments']) == 1


def test_invalid_files_do_not_create_raw_records(capture_manager, tmp_path, monkeypatch):
    manager, _ = capture_manager
    path = tmp_path / '过大附件.txt'
    path.write_bytes(b'oversize')
    monkeypatch.setattr(capture, 'MAX_FILE_SIZE', 4)
    draft = capture.CaptureDraft('这条不应保存。', (path,))
    with pytest.raises(desktop.ControlError, match='最大'):
        draft.submit(manager)
    assert not draft.record_saved and not draft.write_attempted
    manager.start.assert_not_called()


def test_changed_file_is_rejected_before_upload(tmp_path, monkeypatch):
    path = tmp_path / '会变化.txt'
    path.write_bytes(b'original')
    item = capture.inspect_file(path)
    path.write_bytes(b'changed-more')
    connection = Mock()
    monkeypatch.setattr(capture.http.client, 'HTTPConnection', Mock(return_value=connection))
    config = desktop.load_config(tmp_path, {'APP_PORT': '8123'})
    with pytest.raises(desktop.ControlError, match='附件已变化'):
        capture.upload_file(config, '65d79389-05d3-4a72-b54e-90fca60f1eb4', item, 'local-token')
    connection.putrequest.assert_not_called()
    connection.close.assert_called_once()


def test_streaming_multipart_keeps_unicode_filename_and_local_token(client, tmp_path):
    """Exercise the real HTTP encoding, relayed into the isolated FastAPI test app."""
    token = client.headers['x-local-token']
    created = client.post('/api/inbox', json={'content': '流式附件协议测试'}).json()
    selected = tmp_path / '枫叶截图说明.txt'
    selected.write_text('所选文件保持原样。\n第二行。', encoding='utf-8')
    received = {}

    class Relay(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers['Content-Length']))
            received['token'] = self.headers.get('x-local-token')
            received['length'] = len(body)
            response = client.post(self.path, content=body, headers={
                'Content-Type': self.headers['Content-Type'], 'x-local-token': received['token']})
            self.send_response(response.status_code)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(response.content)

        def log_message(self, *args):
            pass

    server = HTTPServer(('127.0.0.1', 0), Relay)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    try:
        config = desktop.load_config(tmp_path, {'APP_PORT': str(server.server_port)})
        result = capture.upload_file(config, created['uuid'], capture.inspect_file(selected), token)
    finally:
        thread.join(timeout=5)
        server.server_close()
    assert received['token'] == token
    assert result['original_filename'] == selected.name
    assert client.get('/api/attachments/' + result['uuid']).content == selected.read_bytes()


@pytest.mark.skipif(os.name != 'nt' and not os.environ.get('DISPLAY'), reason='Tk display unavailable')
def test_capture_window_constructs_without_touching_data_or_starting_service(tmp_path):
    environment = os.environ | {'PLD_HOME': str(tmp_path), 'PYTHONUTF8': '1',
        'DATABASE_URL': 'sqlite:///' + (tmp_path / 'data/personal.db').as_posix()}
    result = subprocess.run([sys.executable, str(desktop.ROOT / 'scripts/desktop_control.py'), '--check-ui'],
                            cwd=desktop.ROOT, env=environment, capture_output=True, text=True,
                            encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stderr
    assert not result.stderr
    ui = json.loads(result.stdout)['quick_capture']
    assert ui['text_editable']
    assert ui['width'] >= 560 and ui['height'] >= 620
    assert ui['save_height'] >= 25
    assert ui['text_height'] >= 80
    assert ui['save_bottom'] <= ui['height'] - 10
    assert ui['minimum']['save_height'] >= 25
    assert ui['minimum']['text_height'] >= 80
    assert ui['minimum']['save_bottom'] <= ui['minimum']['height'] - 10
    assert not list(tmp_path.iterdir())
