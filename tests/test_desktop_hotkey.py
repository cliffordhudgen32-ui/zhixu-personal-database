"""Reserve no real shortcut in automated tests; verify lifecycle through a fake API."""
import os
import queue
import threading

import pytest

from scripts import desktop_hotkey as hotkey


class FakeWindowsAPI:
    def __init__(self, *, conflict=False):
        self.calls = []
        self.messages = queue.Queue()
        self.conflict = conflict
        self.owner = None

    def thread_id(self):
        self.owner = threading.get_ident()
        return 456

    def register(self):
        self.calls.append(('register', threading.get_ident()))
        if self.conflict:
            raise hotkey.HotkeyError('Ctrl + Alt + Space 已被占用，仍可点击“随手记”。')

    def unregister(self):
        self.calls.append(('unregister', threading.get_ident()))

    def next_message(self):
        value = self.messages.get(timeout=5)
        if isinstance(value, Exception):
            raise value
        return value

    def stop(self, owner):
        assert owner == 456
        self.calls.append(('stop', threading.get_ident()))
        self.messages.put(None)
        return True


def test_session_registration_is_idempotent_and_owned_thread_unregisters_exactly_once():
    api = FakeWindowsAPI()
    events = queue.Queue()
    service = hotkey.DesktopHotkey(events.put, api_factory=lambda: api)
    service.start()
    service.start()
    assert service.running
    service.stop()
    service.stop()
    assert not service.running and not service.thread.is_alive()
    assert [name for name, _ in api.calls] == ['register', 'stop', 'unregister']
    assert api.calls[0][1] == api.calls[-1][1] == api.owner
    assert api.owner != threading.get_ident()
    assert events.empty()


def test_shortcut_conflict_is_nonfatal_and_never_unregisters_another_owner():
    api = FakeWindowsAPI(conflict=True)
    events = queue.Queue()
    service = hotkey.DesktopHotkey(events.put, api_factory=lambda: api)
    with pytest.raises(hotkey.HotkeyError, match='仍可点击'):
        service.start()
    service.stop()
    assert not service.running and not service.thread.is_alive()
    assert [name for name, _ in api.calls] == ['register']
    assert events.empty()


def test_only_matching_hotkey_message_invokes_capture_from_worker():
    api = FakeWindowsAPI()
    events = queue.Queue()
    service = hotkey.DesktopHotkey(lambda event: events.put((event, threading.get_ident())),
                                   api_factory=lambda: api)
    service.start()
    try:
        api.messages.put((hotkey.WM_HOTKEY, hotkey.HOTKEY_ID + 1))
        api.messages.put((0x100, hotkey.HOTKEY_ID))
        api.messages.put((hotkey.WM_HOTKEY, hotkey.HOTKEY_ID))
        assert events.get(timeout=2) == ('capture', api.owner)
        assert events.empty()
    finally:
        service.stop()


@pytest.mark.parametrize('failure', [None, hotkey.HotkeyError('模拟消息泵失败')])
def test_unexpected_message_loop_exit_notifies_fallback_and_unregisters(failure):
    api = FakeWindowsAPI()
    events = queue.Queue()
    service = hotkey.DesktopHotkey(events.put, api_factory=lambda: api)
    service.start()
    api.messages.put(failure)
    assert events.get(timeout=2) == 'unavailable'
    service.thread.join(timeout=2)
    service.stop()
    assert not service.running and not service.thread.is_alive()
    assert [name for name, _ in api.calls] == ['register', 'unregister']


def test_stop_before_registration_completes_still_releases_session_registration():
    api = FakeWindowsAPI()
    entered, proceed = threading.Event(), threading.Event()
    original_register = api.register

    def delayed_register():
        entered.set()
        assert proceed.wait(timeout=2)
        original_register()
    api.register = delayed_register
    service = hotkey.DesktopHotkey(lambda event: None, api_factory=lambda: api)
    starter_errors = []

    def start():
        try:
            service.start()
        except hotkey.HotkeyError as exc:
            starter_errors.append(exc)
    starter = threading.Thread(target=start)
    starter.start()
    assert entered.wait(timeout=2)
    service.stopping.set()
    proceed.set()
    starter.join(timeout=2)
    service.stop()
    assert not service.running and not service.registered and not service.thread.is_alive()
    assert [name for name, _ in api.calls] == ['register', 'unregister']
    assert len(starter_errors) == 1


@pytest.mark.skipif(os.name != 'nt', reason='Windows ctypes binding validation')
def test_native_signatures_initialize_without_reserving_system_shortcut():
    api = hotkey._WindowsAPI()
    assert api.thread_id() > 0
    assert api.user.RegisterHotKey.argtypes
    assert api.user.UnregisterHotKey.argtypes
