"""Prevent two application/restore processes from writing the same SQLite store."""
from contextlib import contextmanager
import os
from pathlib import Path

@contextmanager
def process_guard(engine):
    if engine.dialect.name!='sqlite' or engine.url.database==':memory:':
        yield
        return
    target=Path(engine.url.database).resolve().with_suffix('.instance.lock')
    target.parent.mkdir(parents=True,exist_ok=True)
    stream=target.open('a+b')
    locked=False
    try:
        if target.stat().st_size==0:
            stream.write(b'0');stream.flush()
        stream.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            locked=True
        except OSError as exc:
            raise RuntimeError('此数据库已被另一个程序打开。请使用现有网页，或先关闭另一个运行窗口。') from exc
        yield
    finally:
        if locked:
            stream.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
        stream.close()
