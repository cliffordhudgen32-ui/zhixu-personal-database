from contextlib import contextmanager
from threading import RLock
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.engine import make_url
from app.config import DATABASE_URL, HOME

class Base(DeclarativeBase):
    pass

url = make_url(DATABASE_URL)
if url.get_backend_name() == 'sqlite' and url.database and url.database != ':memory:':
    from pathlib import Path
    path = Path(url.database)
    if not path.is_absolute():
        path = HOME / path
    path.parent.mkdir(parents=True, exist_ok=True)
    url = url.set(database=str(path.resolve()))
engine = create_engine(url, connect_args={'check_same_thread': False, 'timeout': 30} if url.get_backend_name() == 'sqlite' else {})
LOCK = RLock()

if engine.dialect.name == 'sqlite':
    @event.listens_for(engine, 'connect')
    def configure_sqlite(connection, record):
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('PRAGMA busy_timeout=30000')

Session = sessionmaker(engine, expire_on_commit=False)

@contextmanager
def transaction():
    with LOCK, Session.begin() as session:
        yield session

def migrate():
    from alembic.config import Config
    from alembic import command
    from app.config import ROOT
    cfg = Config(str(ROOT / 'alembic.ini'))
    cfg.set_main_option('script_location', str(ROOT / 'database'))
    with LOCK:
        command.upgrade(cfg, 'head')
