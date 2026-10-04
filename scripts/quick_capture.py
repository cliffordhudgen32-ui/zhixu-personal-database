"""Local desktop capture: explicit user input, recoverable writes, no import side effects."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import http.client
import json
import mimetypes
from pathlib import Path
import queue
import threading
import uuid
import webbrowser

from scripts.desktop_control import ControlError, page_url, request_json

MAX_FILE_SIZE = 100 * 1024**2
MAX_FILES = 20
MAX_CONTENT = 2_000_000


@dataclass(frozen=True)
class CaptureFile:
    path: Path
    size: int
    sha256: str
    mtime_ns: int

    @property
    def name(self):
        return self.path.name

    def is_uploaded(self, attachments):
        return any(item.get('sha256') == self.sha256 and item.get('file_size') == self.size
                   and item.get('original_filename') == self.name for item in attachments)


def inspect_file(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise ControlError('附件已不存在或不是文件：' + path.name)
    before = path.stat()
    if before.st_size > MAX_FILE_SIZE:
        raise ControlError('单个附件最大 100 MB：' + path.name)
    if len(path.name) > 500 or '\r' in path.name or '\n' in path.name:
        raise ControlError('附件名称过长或格式无效：' + path.name[:80])
    digest = hashlib.sha256()
    with path.open('rb') as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ControlError('附件正在被修改，请稍后重新选择：' + path.name)
    return CaptureFile(path, after.st_size, digest.hexdigest(), after.st_mtime_ns)


def upload_file(config, entry_uuid, item, token, *, timeout=120):
    """Stream a user-selected file to loopback; never buffer a 100 MB attachment."""
    try:
        entry_uuid = str(uuid.UUID(entry_uuid))
    except (ValueError, TypeError, AttributeError):
        raise ControlError('附件记录编号无效。') from None
    filename = item.name.replace('\\', '\\\\').replace('"', '\\"')
    boundary = 'pld-desktop-' + uuid.uuid4().hex
    content_type = mimetypes.guess_type(item.name)[0] or 'application/octet-stream'
    prefix = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
              f'filename="{filename}"\r\nContent-Type: {content_type}\r\n\r\n').encode('utf-8')
    suffix = f'\r\n--{boundary}--\r\n'.encode('ascii')
    connection = http.client.HTTPConnection('127.0.0.1', config.port, timeout=timeout)
    try:
        # Check the opened file before sending headers; replacement or truncation must
        # not produce a malformed request or silently upload different evidence.
        with item.path.open('rb') as source:
            import os
            stat = os.fstat(source.fileno())
            if (stat.st_size, stat.st_mtime_ns) != (item.size, item.mtime_ns):
                raise ControlError('附件已变化，请在网页重新选择上传：' + item.name)
            connection.putrequest('POST', f'/api/entries/{entry_uuid}/attachments')
            connection.putheader('Accept', 'application/json')
            connection.putheader('x-local-token', token)
            connection.putheader('Content-Type', 'multipart/form-data; boundary=' + boundary)
            connection.putheader('Content-Length', str(len(prefix) + item.size + len(suffix)))
            connection.endheaders()
            connection.send(prefix)
            sent = 0
            digest = hashlib.sha256()
            while sent < item.size:
                chunk = source.read(min(1024 * 1024, item.size - sent))
                if not chunk:
                    raise ControlError('附件在上传过程中被修改：' + item.name)
                sent += len(chunk)
                digest.update(chunk)
                connection.send(chunk)
            if digest.hexdigest() != item.sha256:
                raise ControlError('附件在上传过程中被修改：' + item.name)
            connection.send(suffix)
        response = connection.getresponse()
        raw = response.read(65537)
        if len(raw) > 65536:
            raise ControlError('附件响应过大，请在网页核对。')
        try:
            result = json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeError):
            raise ControlError('附件上传结果未能确认，请重试核对。') from None
        if not 200 <= response.status < 300:
            detail = result.get('detail') if isinstance(result, dict) else None
            raise ControlError(detail if isinstance(detail, str) else '附件上传未完成。',
                               status_code=response.status)
        if not isinstance(result, dict) or not item.is_uploaded([result]):
            raise ControlError('附件上传结果与所选文件不一致，请在网页核对。')
        return result
    except (OSError, http.client.HTTPException):
        raise ControlError('附件连接中断，请重试；原文不会再次创建。') from None
    finally:
        connection.close()


@dataclass
class CaptureDraft:
    content: str
    paths: tuple[Path, ...] = ()
    uuid: str = field(default_factory=lambda: str(uuid.uuid4()))
    record_saved: bool = False
    write_attempted: bool = False
    uploaded: set[str] = field(default_factory=set)
    files: tuple[CaptureFile, ...] | None = None

    def submit(self, manager):
        """Keep one UUID through retries, and reconcile uncertain upload responses."""
        if not self.content.strip() or len(self.content) > MAX_CONTENT:
            raise ControlError('请填写记录内容（最多 200 万字）。')
        if len(self.paths) > MAX_FILES:
            raise ControlError('每条随手记最多选择 20 个附件。')
        if self.files is None:
            # Invalid files are caught before creating the raw record.
            self.files = tuple(inspect_file(path) for path in dict.fromkeys(self.paths))
        session = manager.start()
        token = session['token']
        config = manager.config
        endpoint = '/api/entries/' + self.uuid
        if not self.record_saved:
            payload = {'uuid': self.uuid, 'title': self.content.strip().splitlines()[0][:100],
                       'content': self.content, 'item_type': 'inbox', 'origin': 'manual',
                       'source': '桌面随手记', 'is_archived': False}
            try:
                self.write_attempted = True
                record = request_json(config, '/api/entries', method='POST', token=token,
                                      payload=payload, timeout=30)
            except ControlError as first_error:
                # A timeout can occur after a successful commit. Read the permanent
                # UUID before allowing the next retry; a 409 follows the same path.
                try:
                    record = request_json(config, endpoint, timeout=10)
                except ControlError:
                    if first_error.status_code in (400, 403, 404, 413, 422):
                        self.write_attempted = False
                    raise first_error
            if (not isinstance(record, dict) or record.get('uuid') != self.uuid
                    or record.get('content') != self.content or record.get('item_type') != 'inbox'
                    or record.get('deleted_at')):
                raise ControlError('记录保存结果未能确认，请在网页核对后重试。')
            self.record_saved = True
        else:
            record = request_json(config, endpoint, timeout=10)
            if record.get('deleted_at'):
                raise ControlError('该收集记录已在网页移入回收站，请先恢复后补充附件。')
        attachments = record.get('attachments', [])
        for item in self.files:
            if item.is_uploaded(attachments):
                self.uploaded.add(str(item.path))
                continue
            try:
                attachment = upload_file(config, self.uuid, item, token)
            except ControlError as first_error:
                try:
                    recovered = request_json(config, endpoint, timeout=10)
                except ControlError:
                    raise first_error
                attachments = recovered.get('attachments', [])
                if not item.is_uploaded(attachments):
                    raise first_error
            else:
                attachments.append(attachment)
            self.uploaded.add(str(item.path))
        return {'uuid': self.uuid,
                'attachments': len({(item.name, item.size, item.sha256) for item in self.files})}


class QuickCaptureWindow:
    """One reusable capture window. Closing the small window retains unsaved input."""
    def __init__(self, parent, manager, palette, *, on_saved=None):
        import tkinter as tk
        from tkinter import ttk
        self.tk = tk
        self.manager = manager
        self.on_saved = on_saved or (lambda: None)
        self.window = tk.Toplevel(parent)
        self.window.title('随手记 · 知序')
        self.window.geometry('660x650')
        self.window.minsize(560, 620)
        self.window.transient(parent)
        self.window.protocol('WM_DELETE_WINDOW', self.hide)
        self.window.bind('<Escape>', lambda event: self.hide())
        self.window.bind('<Control-Return>', lambda event: self.save())
        self.window.bind('<Control-KP_Enter>', lambda event: self.save())
        self.queue = queue.Queue()
        self.busy = False
        self.draft = None
        self.paths = []
        self.last_uuid = None
        self.closed = False
        from scripts.desktop_clipboard import TemporaryScreenshots
        self.screenshots = TemporaryScreenshots(manager.config.data_dir)
        self.painted = []
        self.buttons = []

        def frame(master):
            widget = tk.Frame(master)
            self.painted.append((widget, {'background': 'panel'}))
            return widget

        def label(master, text=None, variable=None, token='text', **kwargs):
            widget = tk.Label(master, text=text, textvariable=variable, anchor='w',
                              font=('Microsoft YaHei UI', 10), **kwargs)
            self.painted.append((widget, {'background': 'panel', 'foreground': token}))
            return widget

        self.painted.append((self.window, {'background': 'panel'}))
        body = frame(self.window)
        body.pack(fill='both', expand=True, padx=20, pady=18)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)
        heading = label(body, '想到什么，先记下来')
        heading.configure(font=('Microsoft YaHei UI', 17, 'bold'))
        heading.grid(row=0, column=0, sticky='w')
        label(body, '保留原文与附件，AI 稍后整理；由你审核后归档。', token='muted').grid(
            row=1, column=0, sticky='w', pady=(4, 12))
        editor = frame(body)
        editor.grid(row=2, column=0, sticky='nsew')
        self.text = tk.Text(editor, height=9, wrap='word', undo=True, relief='flat',
                            highlightthickness=1, padx=10, pady=10, font=('Microsoft YaHei UI', 11))
        self.text.pack(side='left', fill='both', expand=True)
        self.painted.append((self.text, {'background': 'background', 'foreground': 'text',
                             'insertbackground': 'text', 'selectbackground': 'selection',
                             'selectforeground': 'text', 'highlightbackground': 'border',
                             'highlightcolor': 'accent'}))
        scrollbar = ttk.Scrollbar(editor, orient='vertical', command=self.text.yview)
        scrollbar.pack(side='right', fill='y')
        self.text.configure(yscrollcommand=scrollbar.set)
        label(body, 'Ctrl + V 粘贴文字 · Ctrl + Enter 保存 · Esc 暂时收起', token='muted').grid(
            row=3, column=0, sticky='w', pady=(8, 8))
        files_bar = frame(body)
        files_bar.grid(row=4, column=0, sticky='ew')
        label(files_bar, '附件（可选，单个最大 100 MB）').pack(side='left')
        self.choose_button = ttk.Button(files_bar, text='选择截图 / 文件', command=self.choose_files)
        self.choose_button.pack(side='right')
        self.buttons.append(self.choose_button)
        self.paste_button = ttk.Button(files_bar, text='粘贴截图', command=self.paste_screenshot)
        self.paste_button.pack(side='right', padx=(0, 8))
        self.buttons.append(self.paste_button)
        self.files_list = tk.Listbox(body, height=3, selectmode='extended', relief='flat',
                                    highlightthickness=1, font=('Microsoft YaHei UI', 9))
        self.files_list.grid(row=5, column=0, sticky='ew', pady=(6, 0))
        self.painted.append((self.files_list, {'background': 'background', 'foreground': 'text',
                               'selectbackground': 'selection', 'selectforeground': 'text',
                               'highlightbackground': 'border'}))
        links = frame(body)
        links.grid(row=6, column=0, sticky='ew', pady=(5, 9))
        self.remove_button = ttk.Button(links, text='移除所选附件', command=self.remove_files)
        self.remove_button.pack(side='left')
        self.buttons.append(self.remove_button)
        self.web_button = ttk.Button(links, text='网页快速收集', command=lambda: self.open_page('quick'))
        self.web_button.pack(side='right')
        self.buttons.append(self.web_button)
        self.status = tk.StringVar(value='一句话也可以保存。选择附件后，可以直接保存附件记录。')
        self.status_label = label(body, variable=self.status, token='accent', wraplength=605, justify='left')
        self.status_label.grid(row=7, column=0, sticky='ew', pady=(0, 10))
        body.bind('<Configure>', lambda event: self.status_label.configure(wraplength=max(300, event.width)))
        actions = frame(body)
        actions.grid(row=8, column=0, sticky='ew')
        self.save_button = ttk.Button(actions, text='保存到收集箱', style='Primary.TButton', command=self.save)
        self.save_button.pack(side='right')
        self.buttons.append(self.save_button)
        self.next_button = ttk.Button(actions, text='稍后补附件，记下一条', command=self.next_record)
        self.view_button = ttk.Button(actions, text='查看收集箱', command=lambda: self.open_page('inbox'))
        self.view_button.pack(side='left')
        self.buttons.append(self.view_button)
        # Only the editor shrinks, keeping the save controls visible at Windows DPI.
        self.window.update_idletasks()
        minimum = max(620, body.winfo_reqheight() + 36 - editor.winfo_reqheight() + 90)
        self.window.minsize(560, minimum)
        self.window.geometry(f'660x{max(650, minimum)}')
        self.apply_palette(palette)
        self.window.after(100, self.drain)
        self.show()

    def apply_palette(self, palette):
        for widget, tokens in self.painted:
            widget.configure(**{key: palette[value] for key, value in tokens.items()})

    def show(self):
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()
        self.text.focus_set()

    def hide(self):
        self.window.withdraw()
        return 'break'

    def choose_files(self):
        from tkinter import filedialog
        chosen = filedialog.askopenfilenames(parent=self.window, title='选择随手记附件')
        for raw in chosen:
            path = Path(raw)
            if path not in self.paths:
                self.paths.append(path)
        if len(self.paths) > MAX_FILES:
            self.paths = self.paths[:MAX_FILES]
            self.status.set('每条随手记最多 20 个附件；其余文件请分条记录。')
        self.render_files()

    def paste_screenshot(self):
        if self.busy or self.draft is not None:
            return
        if len(self.paths) >= MAX_FILES:
            self.status.set('每条随手记最多 20 个附件；请先保存当前记录。')
            return
        self.busy = True
        self.controls()
        self.status.set('正在读取这次复制的图片并暂存到本机数据目录…')

        def perform():
            try:
                path = self.screenshots.capture_clipboard()
                self.queue.put(('clipboard', path))
            except Exception as exc:
                self.queue.put(('clipboard_error', str(exc)))
        threading.Thread(target=perform, daemon=True).start()

    def remove_files(self):
        removed = []
        for index in reversed(self.files_list.curselection()):
            removed.append(self.paths.pop(index))
        leftovers = self.screenshots.cleanup(removed)
        if leftovers:
            self.status.set('附件已移除；暂存截图仍在：' + str(self.screenshots.recovery_directory))
        self.render_files()

    def render_files(self):
        self.files_list.delete(0, 'end')
        for path in self.paths:
            mark = '已上传 · ' if self.draft and str(path.resolve()) in self.draft.uploaded else ''
            self.files_list.insert('end', mark + path.name)

    def controls(self):
        for button in self.buttons:
            button.configure(state='disabled' if self.busy else 'normal')
        frozen = self.busy or self.draft is not None
        self.text.configure(state='disabled' if frozen else 'normal')
        self.choose_button.configure(state='disabled' if frozen else 'normal')
        self.paste_button.configure(state='disabled' if frozen else 'normal')
        self.remove_button.configure(state='disabled' if frozen else 'normal')
        self.next_button.configure(state='disabled' if self.busy else 'normal')
        self.save_button.configure(text='重试未完成部分' if self.draft else '保存到收集箱')

    def save(self):
        if self.busy:
            return 'break'
        if self.draft is None:
            content = self.text.get('1.0', 'end-1c')
            if not content.strip() and self.paths:
                content = '附件收集：' + '、'.join(path.name for path in self.paths)
            if not content.strip():
                self.status.set('先输入一句话，或选择截图 / 文件。')
                self.text.focus_set()
                return 'break'
            self.draft = CaptureDraft(content, tuple(self.paths))
        self.busy = True
        self.status.set('正在保存原文与附件到本机收集箱…')
        self.controls()

        def perform():
            try:
                self.queue.put(('saved', self.draft.submit(self.manager)))
            except Exception as exc:
                self.queue.put(('error', str(exc)))
        threading.Thread(target=perform, daemon=True).start()
        return 'break'

    def next_record(self):
        if self.busy or not self.draft or not self.draft.record_saved:
            return
        self.last_uuid = self.draft.uuid
        self.screenshots.cleanup(path for path in self.paths
                                 if str(path.resolve()) in self.draft.uploaded)
        self.reset()
        detail = ('未上传截图保留在：' + str(self.screenshots.recovery_directory)
                  if self.screenshots.owned else '未上传附件可在收集箱补充。')
        self.status.set('原文已保留；' + detail + '继续记下一条。')

    def reset(self):
        self.draft = None
        self.paths.clear()
        self.text.configure(state='normal')
        self.text.delete('1.0', 'end')
        self.text.edit_reset()
        self.next_button.pack_forget()
        self.render_files()
        self.controls()
        self.text.focus_set()

    def drain(self):
        if self.closed:
            return
        try:
            while True:
                kind, result = self.queue.get_nowait()
                self.busy = False
                if kind == 'saved':
                    self.last_uuid = result['uuid']
                    count = result['attachments']
                    leftovers = self.screenshots.cleanup(self.paths)
                    self.reset()
                    self.status.set('已保存到收集箱' + (f'，含 {count} 个附件' if count else '') +
                                    '。可以继续记录，稍后在每日整理中审核。')
                    if leftovers:
                        self.status.set(self.status.get() + '未能清理的暂存截图仍在：' +
                                        str(self.screenshots.recovery_directory))
                    self.on_saved()
                elif kind == 'clipboard':
                    self.paths.append(result)
                    self.render_files()
                    self.status.set('截图已加入附件，保存后才会提交到本机收集箱。')
                elif kind == 'clipboard_error':
                    self.status.set(result)
                elif kind == 'opened':
                    self.status.set('已打开数据库网页。')
                elif kind == 'open_error':
                    self.status.set('网页打开未完成，输入仍保留：' + result)
                else:
                    if self.draft and self.draft.record_saved:
                        self.status.set('原文已保存。附件未全部完成，重试会核对已有附件：' + result)
                        self.next_button.pack(side='left', padx=(8, 0))
                        self.on_saved()
                    else:
                        if self.draft and not self.draft.write_attempted:
                            self.draft = None
                            self.status.set('尚未保存，输入与附件仍保留：' + result)
                        else:
                            self.status.set('保存未确认。输入仍保留，重试使用同一编号：' + result)
                    self.render_files()
                self.controls()
        except queue.Empty:
            pass
        self.window.after(100, self.drain)

    def open_page(self, page):
        if self.busy:
            return
        self.busy = True
        self.controls()
        self.status.set('正在打开数据库网页…')

        def perform():
            try:
                self.manager.start()
                if not self.manager.closing.is_set():
                    webbrowser.open(page_url(self.manager.config, page))
                self.queue.put(('opened', None))
            except Exception as exc:
                self.queue.put(('open_error', str(exc)))
        threading.Thread(target=perform, daemon=True).start()

    def can_close_parent(self):
        from tkinter import messagebox
        if self.busy:
            self.show()
            self.status.set('正在处理随手记，请等操作结束后再关闭控制台。')
            return False
        unsaved = self.text.get('1.0', 'end-1c').strip() or self.paths
        if not unsaved:
            return True
        self.show()
        detail = ('原文已保存，还有未完成的附件。关闭后可在网页补充。' if self.draft and self.draft.record_saved
                  else '随手记窗口还有未保存或未确认的输入。')
        if self.screenshots.owned:
            detail += '\n未上传截图会保留在：' + str(self.screenshots.recovery_directory)
        return messagebox.askokcancel('关闭前确认', detail + '\n仍要关闭控制台吗？', parent=self.window)

    def destroy(self):
        self.closed = True
        self.window.destroy()
