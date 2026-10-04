import json
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / '.env')
HOME = Path(os.getenv('PLD_HOME', ROOT)).resolve()
HOME.mkdir(parents=True, exist_ok=True)
CONFIG = HOME / 'config.json'
DEFAULTS = dict(name='个人数字记忆', nickname='', attachment_dir='data/attachments',
                backup_dir='backups', auto_backup=1, retention=30, theme='system',
                date_format='YYYY-MM-DD', setup_done=False)

def settings():
    result = DEFAULTS.copy()
    if CONFIG.exists():
        result.update(json.loads(CONFIG.read_text(encoding='utf-8')))
    return result

def save_settings(values):
    result = settings() | values
    temp = CONFIG.with_suffix('.tmp')
    temp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(CONFIG)
    return result

def directory(key):
    value = os.getenv(key.upper(), settings()[key])
    path = (HOME / value).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path

DB_PATH = HOME / 'data/personal.db'
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///' + DB_PATH.as_posix())
