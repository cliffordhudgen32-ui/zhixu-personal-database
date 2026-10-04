"""Tray lifecycle is exercised with a fake Windows API, never the user's taskbar."""
import json
import os
import queue
import subprocess
import sys
import threading
from unittest.mock import Mock

import pytest

from scripts import desktop_control as desktop
from scripts import desktop_tray as tray


@pytest.mark.skipif(os.name != 'nt', reason='Windows ctypes binding validation')
def test_native_windows_bindings_initialize_without_creating_icon_or_window():
    api = tray._WindowsAPI()
    assert api.taskbar_created
    assert not hasattr(api, 'callback')


class FakeWindowsAPI:
    taskbar_created = 0xC012

    def __init__(self, *, add_success=True):
        self.add_success = add_success
        self.calls = []
        self.messages = queue.Queue()
        self.callback = None
        self.menu_action = 'capture'

    def create_window(self, class_name, callback):
        self.calls.append('create_window')
        self.callback = callback
        return 123

    def load_icon(self, path):
        self.calls.append('load_icon')
        return 456, True

    def add_icon(self, hwnd, icon):
        assert hwnd == 123 and icon == 456
        self.calls.append('add_icon')
        return self.add_success

    def delete_icon(self, hwnd):
        assert hwnd == 123
        self.calls.append('delete_icon')

    def menu(self, hwnd):
        self.calls.append('menu')
        return self.menu_action

    def message_loop(self):
        while True:
            message = self.messages.get(timeout=5)
            if message is None:
                return
            if message == 'fail':
                raise tray.TrayError('模拟消息泵失败')
            kind, lparam = message
            self.callback(123, kind, 1, lparam)

    def default_proc(self, *args):
        return 0

    def destroy_window(self, hwnd):
        self.calls.append('destroy_window')
        self.callback(hwnd, tray.WM_DESTROY, 0, 0)

    def post_quit(self):
        self.messages.put(None)

    def post_close(self, hwnd):
        self.messages.put((tray.WM_CLOSE, 0))

    def release_icon(self, icon):
        self.calls.append('release_icon')

    def unregister_class(self, name):
        self.calls.append('unregister_class')


def test_tray_registration_is_idempotent_and_exit_removes_owned_resources(tmp_path):
    api = FakeWindowsAPI()
    events = queue.Queue()
    controller = tray.DesktopTray(tmp_path / 'app.ico', events.put,
        backend_factory=lambda path, callback: tray.NativeTrayBackend(path, callback, api_factory=lambda: api))
    controller.start()
    backend = controller.backend
    controller.start()
    assert controller.running
    assert api.calls.count('add_icon') == 1
    controller.stop()
    controller.stop()
    assert not backend.thread.is_alive()
    assert not controller.running
    for operation in ['delete_icon', 'release_icon', 'destroy_window', 'unregister_class']:
        assert api.calls.count(operation) == 1
    assert events.empty()


def test_registration_failure_falls_back_and_cleans_partial_resources(tmp_path):
    api = FakeWindowsAPI(add_success=False)
    events = queue.Queue()
    controller = tray.DesktopTray(tmp_path / 'app.ico', events.put,
        backend_factory=lambda path, callback: tray.NativeTrayBackend(path, callback, api_factory=lambda: api))
    with pytest.raises(tray.TrayError, match='图标'):
        controller.start()
    assert not controller.running and controller.backend is None
    assert 'delete_icon' not in api.calls  # A failed registration never owned a shell icon.
    assert api.calls.count('release_icon') == api.calls.count('unregister_class') == 1
    assert events.empty()


def test_menu_actions_are_delivered_from_native_worker_without_tk_calls(tmp_path):
    api = FakeWindowsAPI()
    events = queue.Queue()
    backend = tray.NativeTrayBackend(tmp_path / 'app.ico',
        lambda action: events.put((action, threading.get_ident())), api_factory=lambda: api)
    backend.start()
    try:
        api.messages.put((tray.WM_TRAY, tray.WM_RBUTTONUP))
        action, thread_id = events.get(timeout=2)
        assert action == 'capture'
        assert thread_id == backend.thread.ident and thread_id != threading.get_ident()
        api.menu_action = 'exit'
        api.messages.put((tray.WM_TRAY, tray.WM_RBUTTONUP))
        assert events.get(timeout=2)[0] == 'exit'
        # The menu merely queues exit; the Tk owner decides whether unsaved work permits it.
        assert backend.running and 'delete_icon' not in api.calls
    finally:
        backend.stop()


def test_native_pump_failure_restores_window_signal_and_removes_icon(tmp_path):
    api = FakeWindowsAPI()
    events = queue.Queue()
    backend = tray.NativeTrayBackend(tmp_path / 'app.ico', events.put, api_factory=lambda: api)
    backend.start()
    api.messages.put('fail')
    assert events.get(timeout=2) == 'unavailable'
    backend.thread.join(timeout=2)
    backend.stop()
    assert not backend.running and not backend.thread.is_alive()
    assert api.calls.count('delete_icon') == 1
    assert api.calls.count('release_icon') == 1
    assert events.empty()


def test_explorer_restart_readds_existing_icon_without_second_window(tmp_path):
    api = FakeWindowsAPI()
    events = queue.Queue()
    backend = tray.NativeTrayBackend(tmp_path / 'app.ico', events.put, api_factory=lambda: api)
    backend.start()
    try:
        api.messages.put((api.taskbar_created, 0))
        # A following click is processed after the re-registration, without polling.
        api.messages.put((tray.WM_TRAY, tray.WM_LBUTTONUP))
        assert events.get(timeout=2) == 'open'
        assert api.calls.count('add_icon') == 2 and api.calls.count('create_window') == 1
    finally:
        backend.stop()
    assert api.calls.count('delete_icon') == 1


def test_pending_review_count_uses_api_without_affecting_database_status(tmp_path, monkeypatch):
    config = desktop.load_config(tmp_path, {'APP_PORT': '8123'})
    manager = desktop.ServiceManager(config)
    manager.session = Mock(return_value={'settings': {'name': '测试'}})
    monkeypatch.setattr(desktop, 'port_open', lambda config: True)
    monkeypatch.setattr(desktop, 'request_json', lambda config, endpoint, **kwargs:
        {'counts': {'pending_review': 7}} if 'drafts' in endpoint else {'total': 3, 'today_count': 1})
    result = manager.status()
    assert result['state'] == 'running' and result['pending_review'] == 7


@pytest.mark.parametrize('response', [None, {}, {'counts': None}, {'counts': {'pending_review': False}},
                                    {'counts': {'pending_review': -1}}, {'counts': {'pending_review': '4'}}])
def test_unavailable_or_invalid_counter_never_reports_zero(tmp_path, monkeypatch, response):
    manager = desktop.ServiceManager(desktop.load_config(tmp_path, {'APP_PORT': '8123'}))
    manager.session = Mock(return_value={'settings': {}})
    monkeypatch.setattr(desktop, 'port_open', lambda config: True)
    monkeypatch.setattr(desktop, 'request_json', lambda config, endpoint, **kwargs:
        response if 'drafts' in endpoint else {'total': 3, 'today_count': 1})
    result = manager.status()
    assert result['state'] == 'running' and result['pending_review'] is None


def test_failed_counter_request_does_not_hide_running_service(tmp_path, monkeypatch):
    manager = desktop.ServiceManager(desktop.load_config(tmp_path, {'APP_PORT': '8123'}))
    manager.session = Mock(return_value={'settings': {}})
    monkeypatch.setattr(desktop, 'port_open', lambda config: True)
    def response(config, endpoint, **kwargs):
        if 'drafts' in endpoint:
            raise desktop.ControlError('草稿接口暂不可用', status_code=503)
        return {'total': 3, 'today_count': 1}
    monkeypatch.setattr(desktop, 'request_json', response)
    assert manager.status()['pending_review'] is None
    assert manager.status()['state'] == 'running'


@pytest.mark.skipif(os.name != 'nt' and not os.environ.get('DISPLAY'), reason='Tk display unavailable')
def test_control_constructs_tray_button_without_enabling_icon_or_service(tmp_path):
    environment = os.environ | {'PLD_HOME': str(tmp_path), 'PYTHONUTF8': '1',
        'DATABASE_URL': 'sqlite:///' + (tmp_path / 'data/personal.db').as_posix()}
    result = subprocess.run([sys.executable, str(desktop.ROOT / 'scripts/desktop_control.py'), '--check-ui'],
                            cwd=desktop.ROOT, env=environment, capture_output=True, text=True,
                            encoding='utf-8', timeout=30)
    assert result.returncode == 0 and not result.stderr, result.stderr
    report = json.loads(result.stdout)
    assert report['tray_enabled'] is False
    assert report['quick_capture']['text_editable']
    assert not list(tmp_path.iterdir())
