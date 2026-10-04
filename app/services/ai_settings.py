"""Database AI preferences and a separate Windows-user-protected API secret."""
import ctypes
import os
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from app.config import HOME
from app.database import transaction
from app.models import now
from app.workflow_models import AIConfiguration

DEFAULTS = {'provider': 'ollama', 'model': 'qwen2.5:1.5b',
            'base_url': 'http://127.0.0.1:11435', 'schedule_enabled': False,
            'daily_time': '21:00', 'auto_after_save': False,
            'sensitivityExclude': True, 'include_attachments': True, 'cloud_consent': False,
            'weekly_enabled': False, 'monthly_enabled': False}
SECRET_PATH = HOME / 'secrets' / 'ai-api-key.dpapi'
PROVIDERS = {'ollama', 'openai_compatible', 'disabled'}


def validate_base_url(provider, value):
    if provider not in PROVIDERS:
        raise ValueError('请选择本机 Ollama、兼容 OpenAI 的服务，或暂不开启。')
    if not isinstance(value, str) or not value.strip() or len(value) > 2000:
        raise ValueError('请填写有效的模型服务地址。')
    value = value.strip()
    if re.search(r'[\x00-\x20\\]', value):
        raise ValueError('模型服务地址不能包含空格、控制字符或反斜线。')
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError('模型服务地址或端口格式无效。') from exc
    if not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise ValueError('服务地址不能包含账户密码、查询参数或片段。')
    if '?' in value or '#' in value:
        raise ValueError('服务地址不能包含查询参数或片段。')
    if provider == 'openai_compatible':
        if parsed.scheme != 'https':
            raise ValueError('云端服务地址必须使用 HTTPS。')
    elif provider == 'ollama':
        if parsed.scheme not in {'http', 'https'} or parsed.hostname.lower() not in {'localhost', '127.0.0.1', '::1'}:
            raise ValueError('本机 Ollama 地址只允许 localhost、127.0.0.1 或 ::1。')
        if parsed.path not in {'', '/'}:
            raise ValueError('Ollama 地址只填写本机主机和端口，无需 API 路径。')
        if port is None:
            host = '[::1]' if parsed.hostname == '::1' else parsed.hostname
            parsed = parsed._replace(netloc=host + ':11435')
    elif parsed.scheme not in {'http', 'https'}:
        raise ValueError('模型服务地址必须为 HTTP 或 HTTPS。')
    return urlunsplit(parsed._replace(path=parsed.path.rstrip('/'), query='', fragment=''))


def _dpapi(value, decrypt=False):
    if os.name != 'nt':
        raise RuntimeError('当前密钥存储采用 Windows DPAPI；此系统可使用无需密钥的本机模型。')
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(value)
    entropy_buffer = ctypes.create_string_buffer(b'PersonalKnowledgeDatabase.AI.Secret.v1')
    source = DataBlob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    entropy = DataBlob(len(entropy_buffer.raw) - 1, ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = DataBlob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        function = crypt.CryptUnprotectData
        function.argtypes = [ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.POINTER(DataBlob), ctypes.c_void_p,
                             ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob)]
        ok = function(ctypes.byref(source), None, ctypes.byref(entropy), None, None, 1, ctypes.byref(target))
    else:
        function = crypt.CryptProtectData
        function.argtypes = [ctypes.POINTER(DataBlob), wintypes.LPCWSTR, ctypes.POINTER(DataBlob), ctypes.c_void_p,
                             ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob)]
        ok = function(ctypes.byref(source), '个人数据库 AI 密钥', ctypes.byref(entropy), None, None, 1, ctypes.byref(target))
    if not ok:
        raise RuntimeError('无法读取或保存受 Windows 账户保护的 AI 密钥，请在设置页重新填写。')
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(ctypes.cast(target.data, ctypes.c_void_p))


def _read_secret():
    if not SECRET_PATH.is_file():
        return ''
    encrypted = SECRET_PATH.read_bytes()
    if len(encrypted) > 100_000:
        raise ValueError('密钥文件格式无效，请在设置页重新填写。')
    return _dpapi(encrypted, decrypt=True).decode('utf-8')


def _write_secret(value):
    SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
    encrypted = _dpapi(value.encode('utf-8'))
    temporary = SECRET_PATH.with_suffix('.tmp')
    try:
        temporary.write_bytes(encrypted)
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        temporary.replace(SECRET_PATH)
    finally:
        temporary.unlink(missing_ok=True)


def _values():
    with transaction() as session:
        row = session.get(AIConfiguration, 1)
        saved = row.values if row else {}
        return dict(DEFAULTS, **{key: value for key, value in (saved or {}).items() if key in DEFAULTS})


def get_config():
    """Private runtime configuration; callers must never return this from a route."""
    result = _values()
    result['base_url'] = validate_base_url(result['provider'], result['base_url'])
    result['api_key'] = _read_secret() if result['provider'] == 'openai_compatible' else ''
    return result


def public_settings():
    result = _values()
    result['api_key_set'] = SECRET_PATH.is_file()
    result['secret_storage'] = 'windows_dpapi'
    return result


def save_config(payload):
    if not isinstance(payload, dict) or set(payload) - (set(DEFAULTS) | {'api_key'}):
        raise ValueError('AI 设置含有未知字段。')
    # Validate all fields before touching a stored secret or database preferences.
    with transaction() as session:
        row = session.get(AIConfiguration, 1)
        previous = dict(DEFAULTS, **{key: value for key, value in ((row.values if row else {}) or {}).items() if key in DEFAULTS})
        result = previous | {key: value for key, value in payload.items() if key in DEFAULTS}
        if result['provider'] not in PROVIDERS:
            raise ValueError('AI 使用方式无效。')
        for key in ['schedule_enabled', 'auto_after_save', 'sensitivityExclude', 'include_attachments', 'cloud_consent', 'weekly_enabled', 'monthly_enabled']:
            if not isinstance(result[key], bool):
                raise ValueError('开关设置必须为 true 或 false。')
        if not isinstance(result['daily_time'], str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', result['daily_time']):
            raise ValueError('每日整理时间需为有效的 HH:MM。')
        from app.services.local_ai import validate_model
        result['model'] = validate_model(result['model'].strip() if isinstance(result['model'], str) else result['model'],
                                         local=result['provider'] != 'openai_compatible')
        result['base_url'] = validate_base_url(result['provider'], result['base_url'])
        if result['provider'] == 'openai_compatible' and not result['cloud_consent']:
            raise ValueError('启用云端整理前，请勾选同意将所选资料发送至该服务。')
        key = payload.get('api_key', '')
        if not isinstance(key, str) or len(key) > 8192 or any(ord(char) < 32 for char in key):
            raise ValueError('API Key 格式无效。')
        if key.strip():
            _write_secret(key.strip())
        if row is None:
            row = AIConfiguration(id=1, values=result, updated_at=now())
            session.add(row)
        else:
            row.values = result
            row.updated_at = now()
    return public_settings()
