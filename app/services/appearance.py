"""Local, shared visual preferences; independent of records and AI settings."""
import json
import os
from pathlib import Path
import tempfile
from threading import RLock

SKINS = {'maple-light', 'maple-dark', 'classic'}
DEFAULTS = {'skin': 'classic', 'illustrations': False, 'motion': False}
_LOCK = RLock()


def _preference_path(home=None):
    if home is None:
        from app.config import HOME
        home = HOME
    return Path(home).resolve() / 'appearance.json'


def validate_preferences(payload):
    if not isinstance(payload, dict) or set(payload) - set(DEFAULTS):
        raise ValueError('皮肤设置包含未知字段')
    if 'skin' in payload and (not isinstance(payload['skin'], str) or payload['skin'] not in SKINS):
        raise ValueError('请选择晨光、暮色或素白皮肤')
    for key in ('illustrations', 'motion'):
        if key in payload and not isinstance(payload[key], bool):
            raise ValueError('插画与动态开关必须为布尔值')
    return dict(payload)


def read_preferences(home=None):
    path = _preference_path(home)
    with _LOCK:
        try:
            if path.stat().st_size > 16_384:
                return dict(DEFAULTS)
            stored = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(stored, dict):
                return dict(DEFAULTS)
            # Ignore future preference fields when reading, reject them at the API.
            values = {key: stored[key] for key in DEFAULTS if key in stored}
            return DEFAULTS | validate_preferences(values)
        except (OSError, ValueError, TypeError):
            return dict(DEFAULTS)


def save_preferences(payload, home=None):
    values = validate_preferences(payload)
    path = _preference_path(home)
    with _LOCK:
        result = read_preferences(home) | values
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                    prefix='.appearance-', suffix='.tmp', dir=path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(result, stream, ensure_ascii=False, indent=2)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return result
