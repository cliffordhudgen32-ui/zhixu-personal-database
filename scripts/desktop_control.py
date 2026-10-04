"""Local Tk control window. Importing helpers does not create a Tk window or write data."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
import json
import os
from pathlib import Path
import queue
import re
import socket
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import uuid
import webbrowser

ROOT = Path(__file__).resolve().parent.parent
PAGES = {'dashboard', 'quick', 'inbox', 'entries', 'tasks', 'search', 'export', 'settings', 'knowledge', 'cases',
         'ai-workflow', 'ai-settings', 'semantic-search'}

# These palettes also work when the database service is not running. Artwork is
# loaded locally and scaled only for display; the user's images remain intact.
SKINS = {
    'maple-light': {
        'name': '枫笺 · 晨光', 'caption': '将日常写成自己的经验。', 'art': None,
        'background': '#f7f3ec', 'panel': '#fffcf7', 'soft': '#eee4d8', 'border': '#dccdbb',
        'text': '#3c302b', 'muted': '#716258', 'accent': '#a4422c', 'active': '#8d3423',
        'accent_text': '#ffffff', 'leaf': '#c57552', 'selection': '#f5dfcf',
    },
    'maple-dark': {
        'name': '枫影 · 暮色', 'caption': '灯下整理，也让思绪慢下来。', 'art': None,
        'background': '#211d1b', 'panel': '#2c2521', 'soft': '#3b3029', 'border': '#5f493b',
        'text': '#f4e8d8', 'muted': '#c9b6a1', 'accent': '#e6a078', 'active': '#f2ba95',
        'accent_text': '#291c16', 'leaf': '#b6774b', 'selection': '#5b3e30',
    },
    'classic': {
        'name': '知序 · 素白', 'caption': '清晰记录，从容管理。', 'art': None,
        'background': '#f3f5f2', 'panel': '#ffffff', 'soft': '#e5ebe5', 'border': '#ccd7ce',
        'text': '#263b35', 'muted': '#56675f', 'accent': '#26705c', 'active': '#205b4b',
        'accent_text': '#ffffff', 'leaf': '#718c78', 'selection': '#dcece3',
    },
}


def appearance_preferences(config):
    """Read the shared, non-sensitive skin file only when the UI needs it."""
    from app.services.appearance import read_preferences
    return read_preferences(home=config.home)


def save_appearance_preferences(config, values):
    from app.services.appearance import save_preferences
    return save_preferences(values, home=config.home)


def skin_art_path(config, skin, illustrations=True):
    """Use a fixed asset name; no stored preference can select arbitrary files."""
    artwork = SKINS.get(skin, SKINS['maple-light'])['art']
    return config.root / 'app/static/skins' / artwork if artwork and illustrations else None


def maple_points(x, y, size, rotation=0):
    """Small vector maple motif shared by the desktop panels."""
    import math
    outline = [(0, -1), (.18, -.45), (.48, -.64), (.39, -.27), (.9, -.38), (.68, .04),
               (1, .15), (.43, .39), (.49, .65), (.08, .5), (.08, 1), (-.08, 1),
               (-.08, .5), (-.49, .65), (-.43, .39), (-1, .15), (-.68, .04),
               (-.9, -.38), (-.39, -.27), (-.48, -.64), (-.18, -.45)]
    angle = math.radians(rotation)
    return tuple(coordinate for px, py in outline for coordinate in (
        x + size * (px * math.cos(angle) - py * math.sin(angle)),
        y + size * (px * math.sin(angle) + py * math.cos(angle))))


class ControlError(RuntimeError):
    def __init__(self, message, *, status_code=None):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class RuntimeConfig:
    root: Path
    home: Path
    port: int
    name: str
    database_path: Path
    attachment_dir: Path
    backup_dir: Path

    @property
    def base_url(self):
        return f'http://127.0.0.1:{self.port}'

    @property
    def data_dir(self):
        return self.database_path.parent

    @property
    def log_path(self):
        return self.home / 'logs' / 'desktop-server.log'


def load_config(root=ROOT, environ=None):
    root = Path(root).resolve()
    if environ is None:
        from dotenv import load_dotenv
        load_dotenv(root / '.env')
        environ = os.environ
    if environ.get('APP_HOST', '127.0.0.1') not in ('127.0.0.1', 'localhost'):
        raise ControlError('桌面入口使用本机 IPv4。请在 .env 设置 APP_HOST=127.0.0.1。')
    try:
        port = int(environ.get('APP_PORT', '8000'))
        if not 1 <= port <= 65535:
            raise ValueError()
    except (TypeError, ValueError):
        raise ControlError('APP_PORT 必须是 1 至 65535 的端口数字。') from None
    home = Path(environ.get('PLD_HOME', root))
    home = (root / home).resolve()
    values = {'name': '个人数字记忆', 'attachment_dir': 'data/attachments', 'backup_dir': 'backups'}
    config_file = home / 'config.json'
    try:
        if config_file.exists():
            stored = json.loads(config_file.read_text(encoding='utf-8'))
            if not isinstance(stored, dict):
                raise ValueError()
            values.update(stored)
    except (OSError, ValueError):
        raise ControlError('无法读取 config.json，请保留该文件并检查配置。') from None
    database_url = environ.get('DATABASE_URL', 'sqlite:///' + (home / 'data/personal.db').as_posix())
    if not database_url.startswith('sqlite:///') or database_url[len('sqlite:///'):] == ':memory:':
        raise ControlError('当前桌面入口支持磁盘上的本机 SQLite 数据库。')
    database = (home / Path(database_url[len('sqlite:///'):])).resolve()
    try:
        attachment = (home / environ.get('ATTACHMENT_DIR', values['attachment_dir'])).resolve()
        backup = (home / environ.get('BACKUP_DIR', values['backup_dir'])).resolve()
    except TypeError:
        raise ControlError('附件或备份目录配置无效，请检查 config.json。') from None
    return RuntimeConfig(root, home, port, str(values['name']), database, attachment, backup)


def page_url(config, page='dashboard', query=None):
    if page not in PAGES:
        raise ControlError('页面地址不在本地入口范围内。')
    suffix = '?' + urlencode(query, quote_via=quote) if query else ''
    return config.base_url + '/#' + page + suffix


def valid_session(value):
    return (isinstance(value, dict) and isinstance(value.get('token'), str)
            and len(value['token']) >= 20 and isinstance(value.get('settings'), dict)
            and value.get('version') in ('1.0.0', '1.1.0'))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, new_url):
        raise ControlError('本地服务返回了重定向，请检查端口是否被其他程序占用。')


def request_json(config, endpoint, *, method='GET', token=None, payload=None, timeout=5):
    if not endpoint.startswith('/api/') or '://' in endpoint or '..' in endpoint:
        raise ControlError('只允许访问本机数据库 API。')
    headers = {'Accept': 'application/json'}
    raw = None
    if payload is not None:
        raw = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    if token:
        headers['x-local-token'] = token
    request = Request(config.base_url + endpoint, raw, headers, method=method)
    # Ignore proxy environment variables; the request must stay on loopback.
    opener = build_opener(ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read(4 * 1024**2 + 1)
        if len(body) > 4 * 1024**2:
            raise ControlError('服务响应过大，请在数据库网页查看详情。')
        return json.loads(body.decode('utf-8'))
    except HTTPError as exc:
        try:
            detail = json.loads(exc.read(65536)).get('detail')
        except (OSError, ValueError):
            detail = None
        raise ControlError(detail if isinstance(detail, str) else f'本地请求未完成（HTTP {exc.code}）。',
                           status_code=exc.code) from None
    except (URLError, OSError):
        raise ControlError('无法连接本地数据库，请启动服务或查看日志。') from None
    except (ValueError, UnicodeError):
        raise ControlError('此端口没有返回有效数据库数据。') from None


def port_open(config):
    with socket.socket() as connection:
        connection.settimeout(.5)
        return connection.connect_ex(('127.0.0.1', config.port)) == 0


def stop_path_valid(path, home):
    path = Path(path)
    return (path.is_absolute() and path.parent.resolve() == (Path(home) / 'logs').resolve()
            and re.fullmatch(r'\.desktop-stop-[0-9a-f]{32}', path.name) is not None
            and not path.is_symlink())


def server_command(config, stop_file, executable=None):
    if not stop_path_valid(stop_file, config.home):
        raise ControlError('后台服务停止文件路径无效。')
    executable = Path(executable or sys.executable)
    if executable.name.casefold() == 'pythonw.exe' and executable.with_name('python.exe').exists():
        executable = executable.with_name('python.exe')
    return [str(executable), str(config.root / 'scripts/desktop_control.py'), '--serve',
            '--port', str(config.port), '--stop-file', str(stop_file)]


class ServiceManager:
    def __init__(self, config):
        self.config = config
        self.process = None
        self.stop_file = None
        self.lock = threading.Lock()
        self.identity_lock = threading.Lock()
        self.verified_token = None
        self.closing = threading.Event()

    def owns_running_service(self):
        return self.process is not None and self.process.poll() is None

    def session(self):
        result = request_json(self.config, '/api/session')
        if not valid_session(result):
            raise ControlError(f'端口 {self.config.port} 被其他程序占用，请修改 .env 中的 APP_PORT。')
        with self.identity_lock:
            if self.verified_token != result['token']:
                system = request_json(self.config, '/api/system', timeout=30)
                actual = system.get('database_path') if isinstance(system, dict) else None
                if not isinstance(actual, str) or Path(actual).resolve() != self.config.database_path:
                    raise ControlError(f'端口 {self.config.port} 正在运行另一份数据库。请关闭那份服务或修改 '
                                       '.env 中的 APP_PORT；本窗口未停止或写入其他数据库。')
                self.verified_token = result['token']
        return result

    def status(self):
        if not port_open(self.config):
            return {'state': 'starting' if self.owns_running_service() else 'stopped'}
        try:
            session = self.session()
            stats = request_json(self.config, '/api/stats', timeout=10)
            pending_review = None
            try:
                drafts = request_json(self.config, '/api/ai/drafts?size=1', timeout=10)
                counts = drafts.get('counts') if isinstance(drafts, dict) else None
                count = counts.get('pending_review') if isinstance(counts, dict) else None
                if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                    pending_review = count
            except ControlError:
                # A draft-counter failure must not label the database stopped or
                # silently display zero pending reviews.
                pass
            return {'state': 'running', 'owned': self.owns_running_service(), 'stats': stats,
                    'name': session['settings'].get('name', self.config.name),
                    'pending_review': pending_review}
        except ControlError as exc:
            return {'state': 'blocked', 'error': str(exc)}

    def start(self, timeout=180):
        with self.lock:
            if self.closing.is_set():
                raise ControlError('控制台正在关闭，未启动新服务。')
            if port_open(self.config):
                return self.session()
            if not self.owns_running_service():
                self.config.log_path.parent.mkdir(parents=True, exist_ok=True)
                self.stop_file = self.config.home / 'logs' / ('.desktop-stop-' + uuid.uuid4().hex)
                command = server_command(self.config, self.stop_file)
                environment = os.environ.copy()
                environment['PYTHONUTF8'] = '1'
                with self.config.log_path.open('ab', buffering=0) as output:
                    self.process = subprocess.Popen(command, cwd=self.config.root, env=environment,
                        stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            process = self.process
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise ControlError('数据库服务已退出，请查看 logs/desktop-server.log 或 logs/app.log。')
            if port_open(self.config):
                try:
                    return self.session()
                except ControlError:
                    pass
            time.sleep(.25)
        raise ControlError('启动仍未完成，可能正在备份或升级。稍后刷新状态，或查看日志。')

    def close(self):
        with self.lock:
            self.closing.set()
        return self.stop()

    def stop(self, timeout=20):
        with self.lock:
            if not self.owns_running_service():
                return '服务由其他入口启动，本窗口没有停止它。'
            process, stop_file = self.process, self.stop_file
            if not stop_path_valid(stop_file, self.config.home):
                raise ControlError('停止文件路径异常，未停止服务。')
            stop_file.touch(exist_ok=True)
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return '停止请求已发送，服务会完成当前操作后退出。'
        stop_file.unlink(missing_ok=True)
        return '本窗口启动的数据库服务已正常停止。'

    def backup(self):
        session = self.start()
        result = request_json(self.config, '/api/backups', method='POST', token=session['token'],
                              payload={}, timeout=600)
        return result['name']


def run_server(port, stop_file):
    """Worker dependency imports are kept out of the desktop control process."""
    config = load_config()
    if port != config.port or not stop_path_valid(stop_file, config.home):
        raise ControlError('后台服务配置不一致，未启动。')
    import uvicorn
    server = uvicorn.Server(uvicorn.Config('app.main:app', host='127.0.0.1', port=port,
        workers=1, access_log=False, lifespan='on'))

    def watch_stop():
        while not server.should_exit:
            if Path(stop_file).exists():
                server.should_exit = True
                return
            time.sleep(.2)

    threading.Thread(target=watch_stop, daemon=True).start()
    try:
        server.run()
    finally:
        Path(stop_file).unlink(missing_ok=True)


def run_window(config, *, autostart=False, check_ui=False):
    import tkinter as tk
    from tkinter import messagebox, ttk
    window = tk.Tk()
    window.title('知序 · 每日数据库控制台')
    try:
        window.iconbitmap(str(config.root / 'app/static/app.ico'))
    except tk.TclError:
        pass
    window.geometry('1100x820')
    window.minsize(1020, 780)
    style = ttk.Style(window)
    style.theme_use('clam')
    try:
        preferences = appearance_preferences(config)
        appearance_error = None
    except (OSError, ValueError) as exc:
        preferences = {'skin': 'maple-light', 'illustrations': True, 'motion': False}
        appearance_error = str(exc)
    manager = ServiceManager(config)
    results = queue.Queue()
    buttons = []
    painted = []
    state = {'busy': False, 'refreshing': False, 'closing': False,
             'appearance': preferences, 'art_photo': None, 'art_cache': {}, 'leaf_step': 0,
             'capture': None, 'tray': None, 'hotkey': None}

    def surface(parent, background='background', **options):
        widget = tk.Frame(parent, **options)
        painted.append((widget, {'background': background}))
        return widget

    def label(parent, text=None, *, variable=None, token='text', background='background',
              font=('Microsoft YaHei UI', 10), **options):
        widget = tk.Label(parent, text=text, textvariable=variable, font=font, anchor='w', **options)
        painted.append((widget, {'background': background, 'foreground': token}))
        return widget

    frame = surface(window)
    frame.pack(fill='both', expand=True, padx=24, pady=24)
    frame.columnconfigure(0, minsize=270)
    frame.columnconfigure(1, weight=1)
    frame.rowconfigure(0, weight=1)
    companion = surface(frame, 'panel', highlightthickness=1)
    painted.append((companion, {'highlightbackground': 'border'}))
    companion.grid(row=0, column=0, sticky='nsew', padx=(0, 22))
    companion.pack_propagate(False)
    companion.configure(width=270)
    brand = surface(companion, 'panel')
    brand.pack(fill='x', padx=22, pady=(20, 0))
    label(brand, '知序', background='panel', font=('Microsoft YaHei UI', 26, 'bold')).pack(anchor='w')
    label(brand, 'PERSONAL KNOWLEDGE', token='muted', background='panel',
          font=('Microsoft YaHei UI', 8)).pack(anchor='w', pady=(1, 4))
    art = tk.Canvas(companion, width=248, height=340, highlightthickness=0)
    art.pack(fill='x', padx=10, pady=(12, 0))
    painted.append((art, {'background': 'panel'}))
    caption = tk.StringVar()
    label(companion, variable=caption, token='muted', background='panel', wraplength=230,
          justify='left', font=('Microsoft YaHei UI', 10)).pack(anchor='w', padx=22, pady=(12, 8))
    separator = surface(companion, 'border', height=1)
    separator.pack(fill='x', padx=22, pady=(6, 14))
    label(companion, '我的界面', background='panel', font=('Microsoft YaHei UI', 11, 'bold')).pack(
        anchor='w', padx=22)
    theme_text = tk.StringVar()
    skin_names = [value['name'] for value in SKINS.values()]
    skin_ids = {value['name']: key for key, value in SKINS.items()}
    chooser = ttk.Combobox(companion, textvariable=theme_text, values=skin_names,
                           state='readonly', style='Skin.TCombobox', font=('Microsoft YaHei UI', 10))
    chooser.pack(fill='x', padx=22, pady=(10, 8), ipady=5)
    illustrations = tk.BooleanVar()
    motion = tk.BooleanVar()
    character_toggle = ttk.Checkbutton(companion, text='显示角色插画', variable=illustrations,
                                      style='Skin.TCheckbutton')
    character_toggle.pack(anchor='w', padx=20, pady=3)
    motion_toggle = ttk.Checkbutton(companion, text='轻柔落叶', variable=motion, style='Skin.TCheckbutton')
    motion_toggle.pack(anchor='w', padx=20, pady=3)
    label(companion, '皮肤设置会与数据库网页同步。', token='muted', background='panel',
          wraplength=230, font=('Microsoft YaHei UI', 9)).pack(anchor='w', padx=22, pady=(10, 0))

    main = surface(frame)
    main.grid(row=0, column=1, sticky='nsew')
    title_row = surface(main)
    title_row.pack(fill='x')
    label(title_row, '我的每日记忆空间', font=('Microsoft YaHei UI', 22, 'bold')).pack(side='left')
    tray_button = ttk.Button(title_row, text='收起到托盘', style='Small.TButton', command=lambda: hide_to_tray())
    tray_button.pack(side='right', padx=(10, 0))
    label(main, '记录今天，找回经验。资料保存在自己的电脑。', token='muted').pack(anchor='w', pady=(5, 13))
    status_text = tk.StringVar(value='正在检查本机数据库…')
    label(main, variable=status_text, token='accent', wraplength=720, justify='left',
          font=('Microsoft YaHei UI', 9)).pack(anchor='w')
    cards = surface(main)
    cards.pack(fill='x', pady=(13, 12))
    today_text, total_text = tk.StringVar(value='—'), tk.StringVar(value='—')
    for index, (title, variable, detail) in enumerate([
            ('今日积累', today_text, '今天留下的记录'), ('全部资料', total_text, '持续积累的个人经验')]):
        card = surface(cards, 'panel', highlightthickness=1)
        painted.append((card, {'highlightbackground': 'border'}))
        card.pack(side='left', fill='x', expand=True, padx=(0, 12 if index == 0 else 0))
        label(card, title, token='muted', background='panel', font=('Microsoft YaHei UI', 9)).pack(
            anchor='w', padx=18, pady=(9, 0))
        label(card, variable=variable, token='accent', background='panel',
              font=('Microsoft YaHei UI', 27, 'bold')).pack(anchor='w', padx=18)
        label(card, detail, token='muted', background='panel', font=('Microsoft YaHei UI', 9)).pack(
            anchor='w', padx=18, pady=(0, 9))
    search = surface(main)
    search.pack(fill='x', pady=(0, 11))
    query = tk.StringVar()
    entry = ttk.Entry(search, textvariable=query, font=('Microsoft YaHei UI', 10))
    entry.pack(side='left', fill='x', expand=True, ipady=7)
    note = tk.StringVar(value='打开入口会自动启动数据库。已有本机服务会直接复用。')
    if appearance_error:
        note.set('皮肤设置暂时无法读取，已使用晨光主题：' + appearance_error)

    def draw_art():
        if state['closing']:
            return
        selected = state['appearance']
        palette = SKINS[selected['skin']]
        width = max(230, art.winfo_width())
        height = max(300, art.winfo_height())
        art.delete('all')
        art.configure(background=palette['panel'])
        picture = skin_art_path(config, selected['skin'], selected['illustrations'])
        displayed = False
        if picture is not None:
            try:
                from PIL import Image, ImageTk
                cache_key = (str(picture), width, height)
                photo = state['art_cache'].get(cache_key)
                if photo is None:
                    with Image.open(picture) as source:
                        image = source.convert('RGB')
                        image.thumbnail((width - 12, height), Image.Resampling.LANCZOS)
                    photo = ImageTk.PhotoImage(image, master=window)
                    # Keep only a few display sizes; resizing never changes source files.
                    if len(state['art_cache']) > 8:
                        state['art_cache'].clear()
                    state['art_cache'][cache_key] = photo
                state['art_photo'] = photo
                art.create_image(width / 2, height / 2, image=photo, anchor='center')
                displayed = True
            except (ImportError, OSError, ValueError, tk.TclError):
                state['art_photo'] = None
        else:
            state['art_photo'] = None
        if not displayed:
            if selected['skin'] == 'classic':
                art.create_line(54, 124, width - 54, 124, fill=palette['border'], width=2)
                art.create_line(54, 214, width - 54, 214, fill=palette['border'], width=2)
                art.create_text(width / 2, height / 2, text='知序', fill=palette['accent'],
                                font=('Microsoft YaHei UI', 35))
            else:
                art.create_polygon(maple_points(width / 2, height / 2 - 10, 60, -12),
                                   fill=palette['selection'], outline=palette['leaf'], width=1)
                art.create_text(width / 2, height / 2 + 90, text='一叶一记 · 日有所积',
                                fill=palette['muted'], font=('Microsoft YaHei UI', 10))
        if selected['skin'] != 'classic':
            art.create_polygon(maple_points(19, 24, 10, -22), fill=palette['leaf'], outline='')
            art.create_polygon(maple_points(width - 18, height - 21, 12, 27),
                               fill=palette['leaf'], outline='')

    def apply_appearance(values):
        skin = values.get('skin', 'maple-light')
        if skin not in SKINS:
            skin = 'maple-light'
        state['appearance'] = {'skin': skin, 'illustrations': bool(values.get('illustrations', True)),
                               'motion': bool(values.get('motion', False))}
        palette = SKINS[skin]
        window.configure(background=palette['background'])
        for widget, tokens in painted:
            widget.configure(**{option: palette[token] for option, token in tokens.items()})
        style.configure('TButton', font=('Microsoft YaHei UI', 10), padding=(9, 10),
                        background=palette['panel'], foreground=palette['text'],
                        bordercolor=palette['border'], lightcolor=palette['panel'],
                        darkcolor=palette['panel'], focuscolor=palette['accent'])
        style.map('TButton', background=[('active', palette['soft']), ('disabled', palette['soft'])],
                  foreground=[('disabled', palette['muted'])])
        style.configure('Small.TButton', font=('Microsoft YaHei UI', 9), padding=(8, 5))
        style.configure('Primary.TButton', background=palette['accent'], foreground=palette['accent_text'],
                        bordercolor=palette['accent'], lightcolor=palette['accent'], darkcolor=palette['accent'])
        style.map('Primary.TButton', background=[('active', palette['active']), ('disabled', palette['soft'])],
                  foreground=[('disabled', palette['muted'])])
        style.configure('TEntry', fieldbackground=palette['panel'], foreground=palette['text'],
                        bordercolor=palette['border'], insertcolor=palette['text'],
                        selectbackground=palette['accent'], selectforeground=palette['accent_text'])
        style.configure('Skin.TCombobox', fieldbackground=palette['panel'], background=palette['soft'],
                        foreground=palette['text'], arrowcolor=palette['text'],
                        bordercolor=palette['border'], selectbackground=palette['panel'],
                        selectforeground=palette['text'])
        style.map('Skin.TCombobox', fieldbackground=[('readonly', palette['panel'])],
                  foreground=[('readonly', palette['text'])],
                  selectbackground=[('readonly', palette['panel'])],
                  selectforeground=[('readonly', palette['text'])])
        window.option_add('*TCombobox*Listbox.background', palette['panel'])
        window.option_add('*TCombobox*Listbox.foreground', palette['text'])
        window.option_add('*TCombobox*Listbox.selectBackground', palette['selection'])
        window.option_add('*TCombobox*Listbox.selectForeground', palette['text'])
        style.configure('Skin.TCheckbutton', font=('Microsoft YaHei UI', 9),
                        background=palette['panel'], foreground=palette['text'],
                        indicatorbackground=palette['panel'], indicatorforeground=palette['accent'])
        style.map('Skin.TCheckbutton', background=[('active', palette['panel'])],
                  foreground=[('disabled', palette['muted'])],
                  indicatorbackground=[('selected', palette['accent'])])
        theme_text.set(palette['name'])
        illustrations.set(state['appearance']['illustrations'])
        motion.set(state['appearance']['motion'])
        motion_toggle.configure(state='disabled' if skin == 'classic' else 'normal')
        caption.set(palette['caption'])
        draw_art()
        if state['capture'] is not None:
            state['capture'].apply_palette(palette)

    def change_appearance():
        previous = state['appearance'].copy()
        values = {'skin': skin_ids.get(theme_text.get(), 'maple-light'),
                  'illustrations': illustrations.get(), 'motion': motion.get()}
        try:
            saved = save_appearance_preferences(config, values)
        except (OSError, ValueError) as exc:
            apply_appearance(previous)
            note.set('皮肤设置未保存：' + str(exc))
            return
        apply_appearance(saved)
        note.set('已切换到「' + SKINS[saved['skin']]['name'] + '」，网页会同步此设置。')

    chooser.bind('<<ComboboxSelected>>', lambda _: change_appearance())
    character_toggle.configure(command=change_appearance)
    motion_toggle.configure(command=change_appearance)
    art.bind('<Configure>', lambda _: draw_art())

    def animate_leaves():
        if state['closing']:
            return
        art.delete('falling-leaf')
        selected = state['appearance']
        if selected['motion'] and selected['skin'] != 'classic':
            state['leaf_step'] += 1
            height = max(300, art.winfo_height())
            width = max(230, art.winfo_width())
            for index, x in enumerate((10, width - 11)):
                y = (state['leaf_step'] * 1.2 + index * height / 2) % (height + 22) - 11
                art.create_polygon(maple_points(x, y, 7, state['leaf_step'] * 2 + index * 24),
                                   fill=SKINS[selected['skin']]['leaf'], outline='', tags='falling-leaf')
        window.after(90 if selected['motion'] and selected['skin'] != 'classic' else 1000,
                     animate_leaves)

    def apply_status(result):
        status = result['state']
        if status == 'running':
            owner = '由本控制台启动' if result['owned'] else '由其他入口启动'
            status_text.set(f'● 已运行 · {result["name"]} · {owner}')
            today_text.set(str(result['stats'].get('today_count', '—')))
            total_text.set(str(result['stats'].get('total', '—')))
            pending = result.get('pending_review')
            review_button.configure(text='每日整理与审核' + (f' · {pending}' if pending is not None else ' · —'))
            if pending is None:
                status_text.set(status_text.get() + ' · 待审核数量暂不可用')
        else:
            status_text.set({'stopped': '○ 尚未运行 · 点击下方入口开始',
                             'starting': '◌ 正在启动 / 完成操作，请稍候',
                             'blocked': '端口或连接异常 · ' + result.get('error', '')}[status])
            today_text.set('—')
            total_text.set('—')
            review_button.configure(text='每日整理与审核')
        stop_button.configure(state='normal' if manager.owns_running_service() and not state['busy'] else 'disabled')

    def refresh():
        if state['refreshing'] or state['closing']:
            return
        state['refreshing'] = True

        def fetch():
            try:
                results.put(('status', manager.status()))
            except Exception as exc:
                results.put(('status', {'state': 'blocked', 'error': str(exc)}))
        threading.Thread(target=fetch, daemon=True).start()

    def launch_job(operation, success, waiting):
        if state['busy'] or state['closing']:
            return
        state['busy'] = True
        note.set(waiting)
        for button in buttons:
            button.configure(state='disabled')

        def execute():
            try:
                results.put(('job', success, operation(), None))
            except Exception as exc:
                results.put(('job', success, None, str(exc)))
        threading.Thread(target=execute, daemon=True).start()

    def navigate(page='dashboard', values=None):
        if values == 'today':
            values = {'start': date.today().isoformat(), 'end': date.today().isoformat()}
        def open_page():
            manager.start()
            if not manager.closing.is_set():
                webbrowser.open(page_url(config, page, values))
        launch_job(open_page, lambda _: note.set('已打开数据库页面，保存后的资料会显示在这里。'), '正在打开本地数据库…')

    def quick_capture(_=None):
        if state['closing']:
            return 'break'
        if state['capture'] is None:
            from scripts.quick_capture import QuickCaptureWindow
            state['capture'] = QuickCaptureWindow(window, manager, SKINS[state['appearance']['skin']],
                                                  on_saved=refresh)
        else:
            state['capture'].show()
        return 'break'

    def restore_control(*, focus=True):
        window.deiconify()
        window.lift()
        if focus:
            window.focus_force()

    def hide_to_tray():
        if state['closing']:
            return
        try:
            if state['tray'] is None:
                from scripts.desktop_tray import DesktopTray
                state['tray'] = DesktopTray(config.root / 'app/static/app.ico',
                                            lambda action: results.put(('tray', action)))
            state['tray'].start()
        except Exception as exc:
            note.set('未能收起到托盘，窗口仍保留：' + str(exc))
            return
        # Withdraw only after Shell_NotifyIcon confirmed registration.
        if state['capture'] is not None:
            state['capture'].hide()
        note.set('已收起到托盘。右键图标可打开控制台、随手记或每日审核。')
        window.withdraw()

    # Keep the existing window-local shortcut. The global shortcut below reserves
    # only one combination during this process; neither feature observes other keys.
    window.bind_all('<Control-Shift-space>', quick_capture)

    search_button = ttk.Button(search, text='全局搜索', command=lambda: navigate('search', {'q': query.get()}))
    search_button.pack(side='left', padx=(10, 0))
    buttons.append(search_button)
    entry.bind('<Return>', lambda event: navigate('search', {'q': query.get()}))
    label(main, '日常工作台', font=('Microsoft YaHei UI', 11, 'bold')).pack(anchor='w', pady=(0, 8))
    actions = surface(main)
    actions.pack(fill='x')
    for index, (title, page, values) in enumerate([
        ('打开数据库', 'dashboard', None), ('随手记', 'quick', None),
        ('每日整理与审核', 'ai-workflow', {'all_dates': '1', 'status': 'pending_review'}), ('今日记录', 'entries', 'today'),
        ('待办任务', 'tasks', None), ('语义搜索', 'semantic-search', None),
        ('知识库', 'knowledge', None), ('案例库', 'cases', None),
        ('AI 模型设置', 'ai-settings', None), ('AI 数据导出', 'export', None),
        ('设置与备份', 'settings', None)]):
        button = ttk.Button(actions, text=title, style='Primary.TButton' if index < 3 else 'TButton',
                            command=quick_capture if page == 'quick' else lambda p=page, v=values: navigate(p, v))
        button.grid(row=index // 4, column=index % 4, sticky='ew',
                    padx=(0, 9 if index % 4 < 3 else 0), pady=(0, 8))
        actions.columnconfigure(index % 4, weight=1)
        buttons.append(button)
        if page == 'ai-workflow':
            review_button = button
    label(main, '服务与资料', font=('Microsoft YaHei UI', 11, 'bold')).pack(anchor='w', pady=(4, 8))
    controls = surface(main)
    controls.pack(fill='x')
    start_button = ttk.Button(controls, text='启动服务', command=lambda: launch_job(manager.start,
        lambda _: note.set('数据库服务已准备好。'), '正在启动，首次启动可能需要备份或升级…'))
    stop_button = ttk.Button(controls, text='停止本窗口服务', command=lambda: launch_job(manager.stop, note.set,
        '正在正常停止，等待当前请求完成…'))
    backup_button = ttk.Button(controls, text='立即完整备份', command=lambda: launch_job(manager.backup,
        lambda name: note.set('完整备份已保存：' + name), '正在备份数据库、附件和配置，请稍候…'))
    refresh_button = ttk.Button(controls, text='刷新状态', command=refresh)
    for index, button in enumerate([start_button, stop_button, backup_button, refresh_button]):
        button.grid(row=0, column=index, sticky='ew', padx=(0, 8 if index < 3 else 0))
        controls.columnconfigure(index, weight=1)
        buttons.append(button)

    def open_directory(kind):
        def perform():
            current = load_config(config.root)
            path = getattr(current, kind)
            path.mkdir(parents=True, exist_ok=True)
            if os.name == 'nt':
                os.startfile(str(path))
            else:
                webbrowser.open(path.as_uri())
        launch_job(perform, lambda _: note.set('已打开本机文件夹。'), '正在打开文件夹…')
    for index, (title, kind) in enumerate([('数据文件夹', 'data_dir'), ('附件文件夹', 'attachment_dir'),
                                          ('备份文件夹', 'backup_dir')]):
        button = ttk.Button(controls, text=title, command=lambda key=kind: open_directory(key))
        button.grid(row=1, column=index, sticky='ew', padx=(0, 8), pady=(8, 0))
        buttons.append(button)
    label(main, variable=note, token='accent', wraplength=720, justify='left',
          font=('Microsoft YaHei UI', 9)).pack(anchor='w', pady=(12, 5))
    footer = label(main, '数据库位置：' + str(config.database_path) + '\n'
                  '本机入口：' + config.base_url + '\n'
                  '随手记：Ctrl + Alt + Space（全局快捷键准备中）\n'
                  '关闭控制台仅停止本窗口启动的服务。备份与原始资料保留。',
                  token='muted', wraplength=720, justify='left', font=('Microsoft YaHei UI', 9))
    footer.pack(anchor='w', pady=(3, 0))

    def hotkey_status(message):
        footer.configure(text='数据库位置：' + str(config.database_path) + '\n'
                         '本机入口：' + config.base_url + '\n' + message + '\n'
                         '关闭控制台仅停止本窗口启动的服务。备份与原始资料保留。')

    def enable_hotkey():
        if state['closing']:
            return
        try:
            from scripts.desktop_hotkey import DesktopHotkey
            state['hotkey'] = DesktopHotkey(lambda action: results.put(('hotkey', action)))
            state['hotkey'].start()
            hotkey_status('随手记：Ctrl + Alt + Space（控制台运行期间，其他软件中也可打开）')
        except Exception as exc:
            hotkey_status(str(exc))

    def resize_labels(_=None):
        available = max(580, main.winfo_width())
        for child in main.winfo_children():
            if isinstance(child, tk.Label):
                child.configure(wraplength=available)
    main.bind('<Configure>', resize_labels)

    def close():
        if state['closing']:
            return
        if state['capture'] is not None and not state['capture'].can_close_parent():
            return
        state['closing'] = True
        if state['capture'] is not None:
            state['capture'].hide()
        for button in buttons:
            button.configure(state='disabled')
        chooser.configure(state='disabled')
        character_toggle.configure(state='disabled')
        motion_toggle.configure(state='disabled')
        tray_button.configure(state='disabled')
        note.set('正在关闭，服务将完成当前操作后正常停止…')

        def shutdown():
            try:
                manager.close()
                results.put(('close', None))
            except Exception as exc:
                results.put(('close', str(exc)))
        threading.Thread(target=shutdown, daemon=True).start()

    def drain():
        try:
            while True:
                item = results.get_nowait()
                if item[0] == 'close':
                    if item[1]:
                        messagebox.showerror('服务停止未完成', item[1], parent=window)
                    if state['capture'] is not None:
                        state['capture'].destroy()
                    if state['tray'] is not None:
                        try:
                            state['tray'].stop()
                        except Exception as exc:
                            messagebox.showerror('托盘清理未完成', str(exc), parent=window)
                    if state['hotkey'] is not None:
                        try:
                            state['hotkey'].stop()
                        except Exception as exc:
                            messagebox.showerror('快捷键清理未完成', str(exc), parent=window)
                    window.destroy()
                    return
                if state['closing']:
                    continue
                if item[0] == 'hotkey':
                    if item[1] == 'capture':
                        restore_control(focus=False)
                        quick_capture()
                    else:
                        hotkey_status('全局快捷键暂时不可用，仍可点击“随手记”。')
                    continue
                if item[0] == 'tray':
                    action = item[1]
                    restore_control(focus=action != 'unavailable')
                    if action == 'capture':
                        quick_capture()
                    elif action == 'review':
                        navigate('ai-workflow', {'all_dates': '1', 'status': 'pending_review'})
                    elif action == 'exit':
                        close()
                    elif action == 'unavailable':
                        note.set('托盘暂时不可用，已恢复控制台窗口。')
                    continue
                if item[0] == 'status':
                    state['refreshing'] = False
                    apply_status(item[1])
                else:
                    _, callback, result, error = item
                    state['busy'] = False
                    for button in buttons:
                        button.configure(state='normal')
                    stop_button.configure(state='normal' if manager.owns_running_service() else 'disabled')
                    if error:
                        note.set(error)
                        messagebox.showerror('操作未完成', error, parent=window)
                    else:
                        callback(result)
                    refresh()
        except queue.Empty:
            pass
        window.after(100, drain)

    def periodic_refresh():
        refresh()
        try:
            current = appearance_preferences(config)
            if current != state['appearance']:
                apply_appearance(current)
        except (OSError, ValueError):
            # A temporary file or external edit must not interrupt database controls.
            pass
        window.after(15000, periodic_refresh)
    window.protocol('WM_DELETE_WINDOW', close)
    apply_appearance(preferences)
    if check_ui:
        checks = []
        for skin in SKINS:
            apply_appearance({**preferences, 'skin': skin})
            for _ in range(3):
                window.update_idletasks()
                window.update()
            checks.append({'skin': skin, 'art_loaded': state['art_photo'] is not None,
                           'width': window.winfo_width(), 'height': window.winfo_height(),
                           'footer_bottom': footer.winfo_rooty() - window.winfo_rooty() + footer.winfo_height(),
                           'buttons': [{'text': button.cget('text'), 'width': button.winfo_width(),
                                        'height': button.winfo_height()} for button in buttons]})
        window.geometry('1020x780')
        window.update_idletasks()
        window.update()
        minimum = {'width': window.winfo_width(), 'height': window.winfo_height(),
                   'footer_bottom': footer.winfo_rooty() - window.winfo_rooty() + footer.winfo_height(),
                   'buttons_right': max(button.winfo_rootx() - window.winfo_rootx()
                                        + button.winfo_width() for button in buttons)}
        quick_capture()
        for _ in range(3):
            window.update_idletasks()
            window.update()
        capture = state['capture']
        capture_report = {'title': capture.window.title(), 'width': capture.window.winfo_width(),
                          'height': capture.window.winfo_height(),
                          'save_bottom': capture.save_button.winfo_rooty() - capture.window.winfo_rooty()
                                         + capture.save_button.winfo_height(),
                          'save_height': capture.save_button.winfo_height(),
                          'text_height': capture.text.winfo_height(),
                          'clipboard_button': capture.paste_button.cget('text'),
                          'text_editable': capture.text.cget('state') == 'normal'}
        minimum_width, minimum_height = capture.window.minsize()
        capture.window.geometry(f'{minimum_width}x{minimum_height}')
        for _ in range(3):
            window.update_idletasks()
            window.update()
        capture_report['minimum'] = {
            'height': capture.window.winfo_height(),
            'save_height': capture.save_button.winfo_height(),
            'text_height': capture.text.winfo_height(),
            'save_bottom': capture.save_button.winfo_rooty() - capture.window.winfo_rooty()
                           + capture.save_button.winfo_height()}
        capture.destroy()
        print(json.dumps({'title': window.title(), 'skin': preferences['skin'],
                          'skins': checks, 'minimum': minimum, 'quick_capture': capture_report,
                          'tray_available': os.name == 'nt', 'tray_enabled': state['tray'] is not None,
                          'hotkey_enabled': state['hotkey'] is not None}, ensure_ascii=False))
        window.destroy()
        return
    window.after(100, drain)
    window.after(100, enable_hotkey)
    window.after(100, periodic_refresh)
    window.after(100, animate_leaves)
    if autostart:
        window.after(200, lambda: launch_job(manager.start,
            lambda _: note.set('数据库服务已准备好。'), '正在启动本机数据库…'))
    window.mainloop()


def main():
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    parser = argparse.ArgumentParser(description='知序桌面控制台')
    parser.add_argument('--serve', action='store_true')
    parser.add_argument('--port', type=int)
    parser.add_argument('--stop-file', type=Path)
    parser.add_argument('--autostart', action='store_true', help='打开控制台后启动本机服务')
    parser.add_argument('--check-ui', action='store_true', help='构造界面并打印尺寸后关闭，不启动服务')
    args = parser.parse_args()
    if args.serve:
        if args.port is None or args.stop_file is None:
            parser.error('后台服务缺少本机端口或停止文件')
        run_server(args.port, args.stop_file)
    else:
        try:
            run_window(load_config(), autostart=args.autostart, check_ui=args.check_ui)
        except Exception as exc:
            import tkinter as tk
            from tkinter import messagebox
            temporary = tk.Tk()
            temporary.withdraw()
            messagebox.showerror('知序控制台无法打开', str(exc), parent=temporary)
            temporary.destroy()


if __name__ == '__main__':
    main()
