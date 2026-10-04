"""Optional Windows notification-area icon. No GUI, API calls, or thread on import."""
from __future__ import annotations

import os
from pathlib import Path
import threading
import uuid


class TrayError(RuntimeError):
    pass


WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_CONTEXTMENU = 0x007B
WM_TRAY = 0x8001
MENU_ACTIONS = {1001: 'open', 1002: 'capture', 1003: 'review', 1004: 'exit'}


class _WindowsAPI:
    """ctypes bindings remain local to the Windows runtime; no extra package needed."""
    def __init__(self):
        if os.name != 'nt':
            raise TrayError('当前系统不支持 Windows 托盘，请继续使用控制台窗口。')
        import ctypes
        from ctypes import wintypes
        self.ctypes = ctypes
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.shell = ctypes.WinDLL('shell32', use_last_error=True)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                                       wintypes.WPARAM, wintypes.LPARAM)

        class WNDCLASS(ctypes.Structure):
            _fields_ = [('style', wintypes.UINT), ('lpfnWndProc', self.WNDPROC),
                        ('cbClsExtra', ctypes.c_int), ('cbWndExtra', ctypes.c_int),
                        ('hInstance', wintypes.HINSTANCE), ('hIcon', wintypes.HICON),
                        ('hCursor', wintypes.HANDLE), ('hbrBackground', wintypes.HBRUSH),
                        ('lpszMenuName', wintypes.LPCWSTR), ('lpszClassName', wintypes.LPCWSTR)]

        class GUID(ctypes.Structure):
            _fields_ = [('Data1', wintypes.DWORD), ('Data2', wintypes.WORD),
                        ('Data3', wintypes.WORD), ('Data4', ctypes.c_ubyte * 8)]

        class NOTIFYICONDATA(ctypes.Structure):
            _fields_ = [('cbSize', wintypes.DWORD), ('hWnd', wintypes.HWND),
                        ('uID', wintypes.UINT), ('uFlags', wintypes.UINT),
                        ('uCallbackMessage', wintypes.UINT), ('hIcon', wintypes.HICON),
                        ('szTip', wintypes.WCHAR * 128), ('dwState', wintypes.DWORD),
                        ('dwStateMask', wintypes.DWORD), ('szInfo', wintypes.WCHAR * 256),
                        ('uTimeoutOrVersion', wintypes.UINT), ('szInfoTitle', wintypes.WCHAR * 64),
                        ('dwInfoFlags', wintypes.DWORD), ('guidItem', GUID), ('hBalloonIcon', wintypes.HICON)]

        self.WNDCLASS = WNDCLASS
        self.NOTIFYICONDATA = NOTIFYICONDATA
        self.MSG = wintypes.MSG
        self.POINT = wintypes.POINT
        # Explicit pointer-sized signatures prevent HWND/HICON truncation on 64-bit Windows.
        signatures = [
            (self.kernel.GetModuleHandleW, [wintypes.LPCWSTR], wintypes.HMODULE),
            (self.user.RegisterClassW, [ctypes.POINTER(WNDCLASS)], wintypes.ATOM),
            (self.user.UnregisterClassW, [wintypes.LPCWSTR, wintypes.HINSTANCE], wintypes.BOOL),
            (self.user.CreateWindowExW, [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID], wintypes.HWND),
            (self.user.DefWindowProcW, [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                      wintypes.LPARAM], ctypes.c_ssize_t),
            (self.user.DestroyWindow, [wintypes.HWND], wintypes.BOOL),
            (self.user.PostMessageW, [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM], wintypes.BOOL),
            (self.user.GetMessageW, [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT,
                                   wintypes.UINT], ctypes.c_int),
            (self.user.TranslateMessage, [ctypes.POINTER(wintypes.MSG)], wintypes.BOOL),
            (self.user.DispatchMessageW, [ctypes.POINTER(wintypes.MSG)], ctypes.c_ssize_t),
            (self.user.PostQuitMessage, [ctypes.c_int], None),
            (self.user.LoadImageW, [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                                  ctypes.c_int, ctypes.c_int, wintypes.UINT], wintypes.HANDLE),
            (self.user.LoadIconW, [wintypes.HINSTANCE, ctypes.c_void_p], wintypes.HICON),
            (self.user.DestroyIcon, [wintypes.HICON], wintypes.BOOL),
            (self.user.RegisterWindowMessageW, [wintypes.LPCWSTR], wintypes.UINT),
            (self.user.CreatePopupMenu, [], wintypes.HMENU),
            (self.user.AppendMenuW, [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR], wintypes.BOOL),
            (self.user.DestroyMenu, [wintypes.HMENU], wintypes.BOOL),
            (self.user.GetCursorPos, [ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
            (self.user.SetForegroundWindow, [wintypes.HWND], wintypes.BOOL),
            (self.user.TrackPopupMenu, [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                ctypes.c_int, wintypes.HWND, wintypes.LPRECT], wintypes.UINT),
            (self.shell.Shell_NotifyIconW, [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATA)], wintypes.BOOL),
        ]
        for function, arguments, result in signatures:
            function.argtypes = arguments
            function.restype = result
        self.instance = self.kernel.GetModuleHandleW(None)
        self.taskbar_created = self.user.RegisterWindowMessageW('TaskbarCreated')

    def create_window(self, class_name, callback):
        self.callback = self.WNDPROC(callback)
        record = self.WNDCLASS()
        record.lpfnWndProc = self.callback
        record.hInstance = self.instance
        record.lpszClassName = class_name
        if not self.user.RegisterClassW(self.ctypes.byref(record)):
            raise TrayError('无法注册托盘消息窗口。')
        # An invisible ordinary window receives TaskbarCreated when Explorer restarts.
        hwnd = self.user.CreateWindowExW(0, class_name, '知序托盘消息窗口', 0,
                                        0, 0, 0, 0, None, None, self.instance, None)
        if not hwnd:
            self.user.UnregisterClassW(class_name, self.instance)
            raise TrayError('无法创建托盘消息窗口。')
        return hwnd

    def load_icon(self, path):
        icon = self.user.LoadImageW(None, str(path), 1, 0, 0, 0x0010 | 0x0040)
        if icon:
            return icon, True
        icon = self.user.LoadIconW(None, self.ctypes.c_void_p(32512))
        if not icon:
            raise TrayError('无法加载托盘图标。')
        return icon, False

    def add_icon(self, hwnd, icon):
        data = self.NOTIFYICONDATA()
        data.cbSize = self.ctypes.sizeof(data)
        data.hWnd, data.hIcon = hwnd, icon
        data.uID, data.uFlags, data.uCallbackMessage = 1, 0x0001 | 0x0002 | 0x0004, WM_TRAY
        data.szTip = '知序 · 个人知识数据库'
        return bool(self.shell.Shell_NotifyIconW(0, self.ctypes.byref(data)))

    def delete_icon(self, hwnd):
        data = self.NOTIFYICONDATA()
        data.cbSize = self.ctypes.sizeof(data)
        data.hWnd, data.uID = hwnd, 1
        self.shell.Shell_NotifyIconW(2, self.ctypes.byref(data))

    def menu(self, hwnd):
        menu = self.user.CreatePopupMenu()
        if not menu:
            raise TrayError('托盘菜单暂时无法打开。')
        try:
            for identifier, label in [(1001, '打开控制台'), (1002, '随手记'),
                                      (1003, '每日整理与审核'), (1004, '退出控制台')]:
                self.user.AppendMenuW(menu, 0, identifier, label)
            point = self.POINT()
            self.user.GetCursorPos(self.ctypes.byref(point))
            self.user.SetForegroundWindow(hwnd)
            selected = self.user.TrackPopupMenu(menu, 0x0100 | 0x0002, point.x, point.y, 0, hwnd, None)
            self.user.PostMessageW(hwnd, 0, 0, 0)
            return MENU_ACTIONS.get(selected)
        finally:
            self.user.DestroyMenu(menu)

    def message_loop(self):
        message = self.MSG()
        while True:
            result = self.user.GetMessageW(self.ctypes.byref(message), None, 0, 0)
            if result == 0:
                return
            if result == -1:
                raise TrayError('托盘消息处理暂时中断。')
            self.user.TranslateMessage(self.ctypes.byref(message))
            self.user.DispatchMessageW(self.ctypes.byref(message))

    def default_proc(self, hwnd, message, wparam, lparam):
        return self.user.DefWindowProcW(hwnd, message, wparam, lparam)

    def destroy_window(self, hwnd):
        self.user.DestroyWindow(hwnd)

    def post_quit(self):
        self.user.PostQuitMessage(0)

    def post_close(self, hwnd):
        self.user.PostMessageW(hwnd, WM_CLOSE, 0, 0)

    def release_icon(self, icon):
        self.user.DestroyIcon(icon)

    def unregister_class(self, name):
        self.user.UnregisterClassW(name, self.instance)


class NativeTrayBackend:
    def __init__(self, icon_path, on_action, *, api_factory=_WindowsAPI):
        self.icon_path = Path(icon_path)
        self.on_action = on_action
        self.api_factory = api_factory
        self.thread = None
        self.ready = threading.Event()
        self.stopping = threading.Event()
        self.running = False
        self.error = None
        self.hwnd = None
        self.api = None
        self.icon = None
        self.icon_owned = False
        self.icon_added = False
        self.cleanup_lock = threading.Lock()
        self.unavailable_sent = False

    def start(self):
        self.thread = threading.Thread(target=self._run, name='zhixu-desktop-tray', daemon=True)
        self.thread.start()
        if not self.ready.wait(timeout=4):
            self.stop()
            raise TrayError('托盘启动未完成，控制台窗口仍保留。')
        if not self.running:
            raise TrayError(self.error or '托盘注册失败，控制台窗口仍保留。')

    def _unavailable(self):
        if not self.unavailable_sent and not self.stopping.is_set():
            self.unavailable_sent = True
            self.on_action('unavailable')

    def _delete_icon(self):
        with self.cleanup_lock:
            if self.icon_added and self.api and self.hwnd:
                self.api.delete_icon(self.hwnd)
                self.icon_added = False

    def _window_proc(self, hwnd, message, wparam, lparam):
        try:
            if message == WM_TRAY:
                if lparam in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                    self.on_action('open')
                elif lparam in (WM_RBUTTONUP, WM_CONTEXTMENU):
                    action = self.api.menu(hwnd)
                    if action:
                        self.on_action(action)
                return 0
            if self.api.taskbar_created and message == self.api.taskbar_created:
                with self.cleanup_lock:
                    self.icon_added = bool(self.api.add_icon(hwnd, self.icon))
                if not self.icon_added:
                    self.error = '系统托盘恢复失败，已返回控制台。'
                    self._unavailable()
                    self.api.post_close(hwnd)
                return 0
            if message == WM_CLOSE:
                self._delete_icon()
                self.api.destroy_window(hwnd)
                return 0
            if message == WM_DESTROY:
                self._delete_icon()
                self.hwnd = None
                self.api.post_quit()
                return 0
            return self.api.default_proc(hwnd, message, wparam, lparam)
        except Exception as exc:
            # Never propagate a Python exception through the Windows callback boundary.
            self.error = str(exc)
            self._unavailable()
            if self.hwnd:
                self.api.post_close(self.hwnd)
            return 0

    def _run(self):
        name = 'ZhixuTray_' + uuid.uuid4().hex
        registered = False
        started = False
        try:
            self.api = self.api_factory()
            self.hwnd = self.api.create_window(name, self._window_proc)
            registered = True
            self.icon, self.icon_owned = self.api.load_icon(self.icon_path)
            with self.cleanup_lock:
                self.icon_added = bool(self.api.add_icon(self.hwnd, self.icon))
            if not self.icon_added:
                raise TrayError('Windows 未能添加托盘图标，控制台窗口仍保留。')
            if self.stopping.is_set():
                return
            self.running = started = True
            self.ready.set()
            self.api.message_loop()
        except Exception as exc:
            self.error = str(exc)
        finally:
            self.running = False
            self._delete_icon()
            if self.hwnd and self.api:
                self.api.destroy_window(self.hwnd)
                self.hwnd = None
            if self.icon_owned and self.icon and self.api:
                self.api.release_icon(self.icon)
            if registered and self.api:
                self.api.unregister_class(name)
            self.ready.set()
            if started:
                self._unavailable()

    def stop(self):
        self.stopping.set()
        if self.hwnd and self.api:
            self.api.post_close(self.hwnd)
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=2)
        # Shell removal also works from the Tk thread if the native pump is delayed.
        self._delete_icon()


class DesktopTray:
    """Idempotent controller, injectable backend for fallback/lifecycle checks."""
    def __init__(self, icon_path, on_action, *, backend_factory=NativeTrayBackend):
        self.icon_path = Path(icon_path)
        self.on_action = on_action
        self.backend_factory = backend_factory
        self.backend = None

    @property
    def running(self):
        return self.backend is not None and self.backend.running

    def start(self):
        if self.running:
            return
        self.stop()
        backend = self.backend_factory(self.icon_path, self.on_action)
        try:
            backend.start()
        except Exception as exc:
            backend.stop()
            raise TrayError(str(exc)) from None
        self.backend = backend

    def stop(self):
        if self.backend is not None:
            self.backend.stop()
            self.backend = None
