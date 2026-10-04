"""One session-only Windows shortcut, without hooks or other-key observation."""
from __future__ import annotations

import os
import threading

HOTKEY_ID = 0x5A01
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000
VK_SPACE = 0x20
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012


class HotkeyError(RuntimeError):
    pass


class _WindowsAPI:
    def __init__(self):
        if os.name != 'nt':
            raise HotkeyError('当前系统不支持此全局快捷键，仍可点击“随手记”。')
        import ctypes
        from ctypes import wintypes
        self.ctypes = ctypes
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.MSG = wintypes.MSG
        signatures = [
            (self.kernel.GetCurrentThreadId, [], wintypes.DWORD),
            (self.user.RegisterHotKey, [wintypes.HWND, ctypes.c_int, wintypes.UINT,
                                       wintypes.UINT], wintypes.BOOL),
            (self.user.UnregisterHotKey, [wintypes.HWND, ctypes.c_int], wintypes.BOOL),
            (self.user.GetMessageW, [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                   wintypes.UINT, wintypes.UINT], ctypes.c_int),
            (self.user.PostThreadMessageW, [wintypes.DWORD, wintypes.UINT,
                                           wintypes.WPARAM, wintypes.LPARAM], wintypes.BOOL),
        ]
        for function, arguments, result in signatures:
            function.argtypes, function.restype = arguments, result

    def thread_id(self):
        return self.kernel.GetCurrentThreadId()

    def register(self):
        if not self.user.RegisterHotKey(None, HOTKEY_ID, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_SPACE):
            code = self.ctypes.get_last_error()
            if code == 1409:
                raise HotkeyError('Ctrl + Alt + Space 已被其他窗口或软件占用，仍可点击“随手记”。')
            raise HotkeyError('全局快捷键暂时不可用，仍可点击“随手记”。')

    def unregister(self):
        self.user.UnregisterHotKey(None, HOTKEY_ID)

    def next_message(self):
        record = self.MSG()
        result = self.user.GetMessageW(self.ctypes.byref(record), None, 0, 0)
        if result == 0:
            return None
        if result == -1:
            raise HotkeyError('全局快捷键消息处理中断，仍可点击“随手记”。')
        return record.message, record.wParam

    def stop(self, thread_id):
        return bool(self.user.PostThreadMessageW(thread_id, WM_QUIT, 0, 0))


class DesktopHotkey:
    def __init__(self, on_action, *, api_factory=_WindowsAPI):
        self.on_action = on_action
        self.api_factory = api_factory
        self.thread = None
        self.ready = threading.Event()
        self.stopping = threading.Event()
        self.running = False
        self.error = None
        self.api = None
        self.thread_id = None
        self.registered = False

    def start(self):
        if self.running:
            return
        if self.thread is not None:
            raise HotkeyError(self.error or '全局快捷键已停止，仍可点击“随手记”。')
        self.thread = threading.Thread(target=self._run, name='zhixu-desktop-hotkey', daemon=True)
        self.thread.start()
        if not self.ready.wait(timeout=4):
            self.stop()
            raise HotkeyError('全局快捷键启动未完成，仍可点击“随手记”。')
        if not self.running:
            raise HotkeyError(self.error or '全局快捷键暂时不可用，仍可点击“随手记”。')

    def _run(self):
        try:
            self.api = self.api_factory()
            self.thread_id = self.api.thread_id()
            self.api.register()
            self.registered = True
            self.running = not self.stopping.is_set()
            self.ready.set()
            while not self.stopping.is_set():
                message = self.api.next_message()
                if message is None:
                    break
                if message == (WM_HOTKEY, HOTKEY_ID):
                    self.on_action('capture')
            if not self.stopping.is_set():
                self.error = '全局快捷键消息处理已停止，仍可点击“随手记”。'
                self.on_action('unavailable')
        except Exception as exc:
            self.error = str(exc)
            if self.running and not self.stopping.is_set():
                self.on_action('unavailable')
        finally:
            self.running = False
            try:
                if self.registered:
                    self.api.unregister()
                    self.registered = False
            finally:
                self.ready.set()

    def stop(self):
        self.stopping.set()
        if self.thread and self.thread.is_alive() and self.api and self.thread_id:
            self.api.stop(self.thread_id)
            if self.thread.ident != threading.get_ident():
                self.thread.join(timeout=4)
            if self.thread.is_alive():
                raise HotkeyError('快捷键注销未完成；控制台退出后 Windows 会释放本进程注册。')
