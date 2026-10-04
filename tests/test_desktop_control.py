"""No display needed: verify loopback control and process ownership boundaries."""
import json
from pathlib import Path
import subprocess
from unittest.mock import Mock
import pytest
from scripts import desktop_control as desktop


@pytest.fixture
def control_config(tmp_path):
    return desktop.load_config(tmp_path, {'APP_PORT': '8123'})


def test_config_uses_local_storage_and_environment(tmp_path):
    (tmp_path / 'config.json').write_text(json.dumps({'name': '我的知识', 'attachment_dir': '资料附件',
        'backup_dir': '我的备份'}, ensure_ascii=False), encoding='utf-8')
    config = desktop.load_config(tmp_path, {'DATABASE_URL': 'sqlite:///other/memory.db', 'BACKUP_DIR': '环境备份'})
    assert config.name == '我的知识'
    assert config.data_dir == tmp_path / 'other'
    assert config.database_path == tmp_path / 'other' / 'memory.db'
    assert config.attachment_dir == tmp_path / '资料附件'
    assert config.backup_dir == tmp_path / '环境备份'
    assert config.base_url == 'http://127.0.0.1:8000'


@pytest.mark.parametrize('environment', [{'APP_HOST': '0.0.0.0'}, {'APP_PORT': '70000'},
    {'APP_PORT': 'bad'}, {'DATABASE_URL': 'postgresql://remote/db'}, {'DATABASE_URL': 'sqlite:///:memory:'}])
def test_reject_nonlocal_or_invalid_configuration(tmp_path, environment):
    with pytest.raises(desktop.ControlError):
        desktop.load_config(tmp_path, environment)


def test_search_url_encodes_query_without_leaving_loopback(control_config):
    url = desktop.page_url(control_config, 'search', {'q': '中文 & # https://example.com'})
    assert url.startswith('http://127.0.0.1:8123/#search?q=')
    assert '%26' in url and '%23' in url and 'https%3A' in url
    with pytest.raises(desktop.ControlError):
        desktop.page_url(control_config, 'https://evil.test')
    with pytest.raises(desktop.ControlError):
        desktop.request_json(control_config, '/api/../private')


def test_session_requires_app_marker_and_write_token():
    assert desktop.valid_session({'version': '1.0.0', 'settings': {}, 'token': 'x' * 32})
    assert not desktop.valid_session({'settings': {}, 'token': 'short'})
    assert not desktop.valid_session({'version': '1.0.0', 'settings': [], 'token': 'x' * 32})


def test_service_identity_is_checked_once_per_session_token(control_config, monkeypatch):
    manager = desktop.ServiceManager(control_config)
    token = {'value': 'x' * 32}
    calls = []
    def response(config, endpoint, **kwargs):
        calls.append(endpoint)
        return ({'version': '1.0.0', 'settings': {}, 'token': token['value']} if endpoint == '/api/session'
                else {'database_path': str(control_config.database_path)})
    monkeypatch.setattr(desktop, 'request_json', response)
    manager.session()
    manager.session()
    assert calls.count('/api/system') == 1
    token['value'] = 'y' * 32
    manager.session()
    assert calls.count('/api/system') == 2


def test_different_database_is_never_reused_or_written(control_config, monkeypatch):
    manager = desktop.ServiceManager(control_config)
    monkeypatch.setattr(desktop, 'port_open', lambda _: True)
    api = Mock(side_effect=[{'version': '1.0.0', 'settings': {}, 'token': 'x' * 32},
                           {'database_path': str(control_config.home / 'other.db')}])
    monkeypatch.setattr(desktop, 'request_json', api)
    popen = Mock()
    monkeypatch.setattr(desktop.subprocess, 'Popen', popen)
    with pytest.raises(desktop.ControlError, match='另一份数据库'):
        manager.backup()
    assert api.call_args_list[-1].args[1] == '/api/system'
    popen.assert_not_called()
    assert not manager.owns_running_service()


def test_server_command_has_private_stop_marker(control_config):
    stop = control_config.home / 'logs' / ('.desktop-stop-' + 'a' * 32)
    command = desktop.server_command(control_config, stop, executable=Path('python.exe'))
    assert '--serve' in command and '--port' in command and command[-1] == str(stop)
    assert command[command.index('--port') + 1] == '8123'
    with pytest.raises(desktop.ControlError):
        desktop.server_command(control_config, control_config.home / 'personal.db')


def test_existing_service_is_reused_and_never_stopped(control_config, monkeypatch):
    manager = desktop.ServiceManager(control_config)
    monkeypatch.setattr(desktop, 'port_open', lambda _: True)
    session = {'version': '1.0.0', 'settings': {}, 'token': 'x' * 32}
    manager.session = Mock(return_value=session)
    popen = Mock()
    monkeypatch.setattr(desktop.subprocess, 'Popen', popen)
    assert manager.start() == session
    assert '其他入口' in manager.stop()
    popen.assert_not_called()
    assert manager.stop_file is None


def test_stop_requests_graceful_exit_only_for_owned_process(control_config):
    manager = desktop.ServiceManager(control_config)
    process = Mock()
    process.poll.return_value = None
    process.wait.side_effect = subprocess.TimeoutExpired('service', 1)
    manager.process = process
    marker = control_config.home / 'logs' / ('.desktop-stop-' + 'b' * 32)
    marker.parent.mkdir(parents=True)
    manager.stop_file = marker
    assert '完成当前操作后退出' in manager.stop(timeout=.01)
    assert marker.exists()
    process.terminate.assert_not_called()
    process.kill.assert_not_called()


def test_backup_fetches_fresh_token_and_posts_to_api(control_config, monkeypatch):
    manager = desktop.ServiceManager(control_config)
    manager.start = Mock(return_value={'token': 'current-local-token'})
    api = Mock(return_value={'name': 'backup_test.zip'})
    monkeypatch.setattr(desktop, 'request_json', api)
    assert manager.backup() == 'backup_test.zip'
    assert api.call_args.args[1] == '/api/backups'
    assert api.call_args.kwargs['method'] == 'POST'
    assert api.call_args.kwargs['token'] == 'current-local-token'
