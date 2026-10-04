"""Consistent snapshots and staged, reversible restore. Single-process writer lock."""
from contextlib import closing
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import uuid
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from fastapi import HTTPException
from app.config import HOME, ROOT, directory, settings, save_settings
from app.database import engine, LOCK, transaction, Base
from app.models import AuditLog, now
from app.services.appearance import read_preferences, save_preferences, validate_preferences

JOURNAL = HOME / '.restore-journal.json'
MAX_FILES = 200000
MAX_BYTES = 50 * 1024**3
MAX_MANIFEST_BYTES = 64 * 1024**2
MAX_CONFIG_BYTES = 1024**2
MAX_APPEARANCE_BYTES = 16_384
HASH_PATTERN = re.compile(r'[0-9a-f]{64}\Z')
RESERVED_NAMES = {'con', 'prn', 'aux', 'nul', 'conin$', 'conout$'} | {
    prefix + digit for prefix in ('com', 'lpt') for digit in '123456789¹²³'
}

def _safe_relative(value):
    """One canonical, portable file name, including on case-insensitive Windows."""
    if not isinstance(value, str) or not value or len(value) > 1500:
        raise HTTPException(400, '备份包含不安全路径')
    p = PurePosixPath(value)
    if not p.parts or p.is_absolute() or str(p) != value or any(part in ('.', '..') for part in p.parts):
        raise HTTPException(400, '备份包含不安全路径')
    for part in p.parts:
        if (part.endswith((' ', '.')) or part.split('.')[0].casefold() in RESERVED_NAMES
                or any(ord(c) < 32 or c in '\\:*?"<>|' for c in part)):
            raise HTTPException(400, '备份包含不安全路径')
    return p

def _readonly(path):
    # mode=ro prevents a missing or misspelled database from becoming an empty one.
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)

def _check_database(conn):
    if (conn.execute('PRAGMA integrity_check').fetchall() != [('ok',)]
            or conn.execute('PRAGMA foreign_key_check').fetchall()):
        raise HTTPException(400, '备份数据库损坏或外键错误')

def _schema(conn):
    return conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master "
                        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()

def _validated_settings(value):
    from app.schemas import SettingsInput
    if not isinstance(value, dict):
        raise HTTPException(400, '备份配置无效')
    try:
        validated = SettingsInput.model_validate(value).model_dump()
    except ValueError as exc:
        raise HTTPException(400, '备份配置无效') from exc
    # Archive directories are never activated; validate portable relative syntax anyway.
    for key in ('attachment_dir', 'backup_dir'):
        _safe_relative(validated[key])
    return validated

def _validated_appearance(value):
    try:
        values = validate_preferences(value)
        if set(values) != {'skin', 'illustrations', 'motion'}:
            raise ValueError('incomplete preferences')
        return values
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, '备份外观设置无效') from exc

def db_path():
    if engine.dialect.name != 'sqlite':
        raise HTTPException(400, '当前备份器仅用于 SQLite；PostgreSQL 请使用 pg_dump')
    if not engine.url.database or engine.url.database == ':memory:':
        raise HTTPException(400, '备份需要磁盘上的 SQLite 数据库')
    return Path(engine.url.database).resolve()

def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()

def safe_attachment(relative):
    base = directory('attachment_dir')
    if isinstance(relative, str):
        relative = relative.replace('\\', '/')
    path = (base / _safe_relative(relative)).resolve()
    if not path.is_relative_to(base) or path == base:
        raise HTTPException(400, '附件路径无效')
    return path

def snapshot(target):
    with closing(_readonly(db_path())) as source, closing(sqlite3.connect(target)) as dest:
        source.backup(dest)
        _check_database(dest)

def create_backup(reason='manual'):
    if reason not in ('manual', 'auto', 'before_restore', 'before_upgrade'):
        raise HTTPException(400, '备份原因无效')
    with LOCK:
        dest = directory('backup_dir') / (f'backup_{datetime.now():%Y-%m-%d_%H%M%S_%f}_{reason}.zip')
        with tempfile.TemporaryDirectory(dir=HOME) as tmp:
            root = Path(tmp)
            snapshot(root / 'personal.db')
            (root / 'config.json').write_text(json.dumps(_validated_settings(settings()), ensure_ascii=False, indent=2), encoding='utf-8')
            (root / 'appearance.json').write_text(json.dumps(
                _validated_appearance(read_preferences(HOME)), ensure_ascii=False, indent=2), encoding='utf-8')
            files = {'personal.db': root / 'personal.db', 'config.json': root / 'config.json',
                     'appearance.json': root / 'appearance.json'}
            expected_hashes = {name: digest(file) for name, file in files.items()}
            with closing(sqlite3.connect(root / 'personal.db')) as conn:
                for relative, expected, size in conn.execute('SELECT file_path,sha256,file_size FROM attachments'):
                    file = safe_attachment(relative)
                    if not file.is_file() or file.stat().st_size != size or digest(file) != expected:
                        raise HTTPException(409, '附件缺失或校验失败，请先检查数据库；未生成不完整备份')
                    name = 'attachments/' + relative.replace('\\', '/')
                    files[name] = file
                    expected_hashes[name] = expected
            if len({name.casefold() for name in files}) != len(files):
                raise HTTPException(409, '附件路径存在大小写冲突，无法生成跨平台备份')
            manifest = {'format': 'pld-backup', 'version': 1, 'created_at': now(),
                        'sha256': expected_hashes}
            temporary = dest.with_suffix('.tmp')
            try:
                with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
                    for name, file in files.items():
                        actual = hashlib.sha256()
                        info = zipfile.ZipInfo.from_file(file, name)
                        info.compress_type = zipfile.ZIP_DEFLATED
                        with file.open('rb') as source, archive.open(info, 'w', force_zip64=True) as output:
                            for chunk in iter(lambda: source.read(1024**2), b''):
                                actual.update(chunk)
                                output.write(chunk)
                        if actual.hexdigest() != manifest['sha256'][name]:
                            raise HTTPException(409, '备份期间文件发生变化，请重试；未生成不完整备份')
                    archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False))
                with temporary.open('r+b') as stream:
                    os.fsync(stream.fileno())
                temporary.replace(dest)
            finally:
                temporary.unlink(missing_ok=True)
        with transaction() as s:
            s.add(AuditLog(action='备份', entity='database', description=dest.name))
        return dest

def backup_before_upgrade():
    """Protect an existing database before Alembic changes its revision."""
    if engine.dialect.name != 'sqlite' or not db_path().is_file():
        return None
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    config = Config(str(ROOT / 'alembic.ini'))
    config.set_main_option('script_location', str(ROOT / 'database'))
    heads = set(ScriptDirectory.from_config(config).get_heads())
    with LOCK, closing(_readonly(db_path())) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='alembic_version'").fetchone():
            return None
        current = {row[0] for row in conn.execute('SELECT version_num FROM alembic_version')}
        if current and current != heads:
            return create_backup('before_upgrade')
    return None

def list_backups():
    return [{'name': p.name, 'size': p.stat().st_size, 'time': datetime.fromtimestamp(p.stat().st_mtime).astimezone().isoformat()}
            for p in sorted(directory('backup_dir').glob('backup_*.zip'), key=lambda p: p.stat().st_mtime, reverse=True)]

def automatic_backup():
    cfg = settings()
    if not cfg['auto_backup']:
        return
    files = [p for p in directory('backup_dir').glob('backup_*_auto.zip')]
    newest = max((p.stat().st_mtime for p in files), default=0)
    if datetime.now().timestamp() - newest >= cfg['auto_backup'] * 86400:
        create_backup('auto')
    files = sorted(directory('backup_dir').glob('backup_*_auto.zip'), key=lambda p: p.stat().st_mtime, reverse=True)
    # Manual and pre-restore backups are never removed by retention.
    for path in files[cfg['retention']:]:
        path.unlink()

def validate_archive(file, stage):
    try:
        return _validate_archive(file, Path(stage))
    except HTTPException:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, sqlite3.Error,
            zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError, EOFError) as exc:
        raise HTTPException(400, '无法读取此备份，当前数据库未改变') from exc

def _validate_archive(file, stage):
    if stage.is_symlink() or (stage.exists() and any(stage.iterdir())):
        raise HTTPException(400, '恢复暂存目录必须为空')
    stage.mkdir(parents=True, exist_ok=True)
    stage = stage.resolve()
    with zipfile.ZipFile(file) as archive:
        infos = archive.infolist()
        total_bytes = sum(x.file_size for x in infos)
        if len(infos) > MAX_FILES or total_bytes > MAX_BYTES:
            raise HTTPException(400, '备份超过恢复安全上限（50 GB / 20 万文件）')
        names = [x.filename for x in infos]
        if len({name.casefold() for name in names}) != len(names):
            raise HTTPException(400, '备份存在重复文件名')
        for item in infos:
            _safe_relative(item.orig_filename)
            _safe_relative(item.filename)
            mode = stat.S_IFMT(item.external_attr >> 16)
            if item.is_dir() or mode not in (0, stat.S_IFREG) or item.flag_bits & 1:
                raise HTTPException(400, '备份包含不安全路径')
        if not {'manifest.json', 'personal.db', 'config.json'}.issubset(names):
            raise HTTPException(400, '备份缺少数据库、配置或清单')
        if (archive.getinfo('manifest.json').file_size > MAX_MANIFEST_BYTES
                or archive.getinfo('config.json').file_size > MAX_CONFIG_BYTES):
            raise HTTPException(400, '备份清单或配置超过安全上限')
        if 'appearance.json' in names and archive.getinfo('appearance.json').file_size > MAX_APPEARANCE_BYTES:
            raise HTTPException(400, '备份外观设置超过安全上限')
        manifest = json.loads(archive.read('manifest.json'))
        if (not isinstance(manifest, dict) or manifest.get('format') != 'pld-backup'
                or type(manifest.get('version')) is not int or manifest.get('version') != 1):
            raise HTTPException(400, '备份格式不兼容')
        hashes = manifest.get('sha256')
        if (not isinstance(hashes, dict) or 'manifest.json' in hashes
                or set(names) != set(hashes) | {'manifest.json'}):
            raise HTTPException(400, '备份文件清单不一致')
        _validated_settings(json.loads(archive.read('config.json')))
        if 'appearance.json' in names:
            _validated_appearance(json.loads(archive.read('appearance.json')))
        if shutil.disk_usage(stage).free < total_bytes + 16 * 1024**2:
            raise HTTPException(400, '磁盘空间不足，无法安全展开备份')
        for name, expected in hashes.items():
            if not isinstance(expected, str) or not HASH_PATTERN.fullmatch(expected):
                raise HTTPException(400, '备份校验清单无效')
            if name not in ('personal.db', 'config.json', 'appearance.json') and not name.startswith('attachments/'):
                raise HTTPException(400, '备份含未知文件')
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(name) as src, target.open('xb') as out:
                written = 0
                while chunk := src.read(1024**2):
                    written += len(chunk)
                    if written > archive.getinfo(name).file_size:
                        raise HTTPException(400, '备份文件大小与清单不一致')
                    out.write(chunk)
                if written != archive.getinfo(name).file_size:
                    raise HTTPException(400, '备份文件大小与清单不一致')
            if digest(target) != expected:
                raise HTTPException(400, '备份校验不通过')
    with closing(_readonly(stage / 'personal.db')) as conn:
        _check_database(conn)
        version = conn.execute('SELECT version_num FROM alembic_version ORDER BY version_num').fetchall()
        with closing(_readonly(db_path())) as current:
            if not version or version != current.execute('SELECT version_num FROM alembic_version ORDER BY version_num').fetchall():
                raise HTTPException(400, '备份版本与当前不一致，请用对应软件版本恢复后再升级')
            if _schema(conn) != _schema(current):
                raise HTTPException(400, '备份表结构与当前软件不一致')
        attachment_names = set()
        for relative, expected, size in conn.execute('SELECT file_path,sha256,file_size FROM attachments'):
            relative = relative.replace('\\', '/') if isinstance(relative, str) else relative
            _safe_relative(relative)
            name = 'attachments/' + relative
            p = stage / name
            if (name not in manifest['sha256'] or not p.is_file()
                    or p.stat().st_size != size or digest(p) != expected):
                raise HTTPException(400, '备份中附件缺失或损坏')
            attachment_names.add(name)
        if attachment_names != {name for name in manifest['sha256'] if name.startswith('attachments/')}:
            raise HTTPException(400, '备份含未登记的附件文件')
    return manifest

def _write_journal(info):
    temporary = JOURNAL.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(info, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(JOURNAL)

def _restore_database(source):
    with closing(_readonly(source)) as src, closing(sqlite3.connect(db_path())) as dest:
        src.backup(dest)
        _check_database(dest)

def _restore_paths(info):
    token = info.get('token')
    if not isinstance(token, str) or not re.fullmatch('[0-9a-f]{32}', token):
        raise RuntimeError('恢复日志无效；请保留日志和回滚文件')
    old_config = _validated_settings(info.get('old_config'))
    appearance = info.get('appearance')
    if 'appearance' in info:
        if (not isinstance(appearance, dict) or set(appearance) != {'existed', 'sha256'}
                or type(appearance.get('existed')) is not bool
                or (appearance['existed'] and (not isinstance(appearance.get('sha256'), str)
                    or not HASH_PATTERN.fullmatch(appearance['sha256'])))
                or (not appearance['existed'] and appearance.get('sha256') is not None)):
            raise RuntimeError('恢复外观日志无效；请保留日志和回滚文件')
    root = (HOME / os.getenv('ATTACHMENT_DIR', old_config['attachment_dir'])).resolve()
    if info.get('attachment_root') != str(root) or info.get('database') != str(db_path()):
        raise RuntimeError('恢复日志与本机存储路径不一致；请使用恢复时的目录配置')
    work = HOME / ('.restore-work-' + token)
    rollback = root.with_name(root.name + '.rollback-' + token)
    incoming = root.with_name(root.name + '.restore-' + token)
    for path in (work, rollback, incoming):
        if path.is_symlink() or path.resolve() != path:
            raise RuntimeError('恢复暂存路径无效；请保留日志和回滚文件')
    _validate_restore_root(root, old_config)
    return root, rollback, incoming, work

def _appearance_snapshot(work):
    """Keep exact original bytes, including a malformed preference file, for rollback."""
    path = HOME / 'appearance.json'
    if path.is_symlink():
        raise HTTPException(400, '外观设置路径无效，当前数据未改变')
    if not path.exists():
        return {'existed': False, 'sha256': None}
    if not path.is_file() or path.stat().st_size > MAX_APPEARANCE_BYTES:
        raise HTTPException(400, '外观设置文件无效或过大，当前数据未改变')
    target = work / 'rollback-appearance.json'
    with path.open('rb') as source, target.open('xb') as dest:
        body = source.read(MAX_APPEARANCE_BYTES + 1)
        if len(body) > MAX_APPEARANCE_BYTES:
            raise HTTPException(400, '外观设置文件过大，当前数据未改变')
        dest.write(body)
        dest.flush()
        os.fsync(dest.fileno())
    return {'existed': True, 'sha256': digest(target)}

def _rollback_appearance(info, work):
    # Old journals and old archives have no appearance change to undo.
    previous = info.get('appearance')
    if previous is None:
        return
    path = HOME / 'appearance.json'
    if not previous['existed']:
        path.unlink(missing_ok=True)
        return
    source = work / 'rollback-appearance.json'
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='wb', prefix='.appearance-restore-', suffix='.tmp',
                dir=HOME, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(source.read_bytes())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

def _validate_restore_root(root, cfg):
    backup_root = (HOME / os.getenv('BACKUP_DIR', cfg['backup_dir'])).resolve()
    protected = (HOME, ROOT / 'app', ROOT / 'database', HOME / '.venv', HOME / '.git',
                 HOME / 'exports', HOME / 'logs', db_path(), backup_root)
    if root == HOME or any(path.is_relative_to(root) or root.is_relative_to(path)
                           for path in protected if path != HOME):
        raise HTTPException(400, '附件目录不能覆盖程序、数据库或备份目录')

def _cleanup_restore(info):
    _, rollback, incoming, work = _restore_paths(info)
    for path in (rollback, incoming, work):
        if path.exists():
            shutil.rmtree(path)
    JOURNAL.unlink(missing_ok=True)

def recover_pending_restore():
    """Call before migrations/startup writes; leave recovery files on any failure."""
    with LOCK:
        if not JOURNAL.exists():
            return False
        if JOURNAL.stat().st_size > MAX_CONFIG_BYTES:
            raise RuntimeError('恢复日志过大；请保留日志和回滚文件')
        info = json.loads(JOURNAL.read_text(encoding='utf-8'))
        if not isinstance(info, dict) or info.get('state') not in ('pending', 'committed', 'rolled_back'):
            raise RuntimeError('恢复日志无效；请保留日志和回滚文件')
        root, rollback, incoming, work = _restore_paths(info)
        if info['state'] == 'pending':
            previous_db = work / 'rollback.db'
            if not previous_db.is_file() or digest(previous_db) != info.get('rollback_sha256'):
                raise RuntimeError('恢复回滚快照缺失或损坏；请保留恢复前完整备份')
            appearance = info.get('appearance')
            if appearance is not None and appearance['existed']:
                previous_appearance = work / 'rollback-appearance.json'
                if (not previous_appearance.is_file() or previous_appearance.is_symlink()
                        or previous_appearance.stat().st_size > MAX_APPEARANCE_BYTES
                        or digest(previous_appearance) != appearance['sha256']):
                    raise RuntimeError('恢复外观回滚快照缺失或损坏；请保留日志和回滚文件')
            engine.dispose()
            try:
                _restore_database(previous_db)
                if rollback.exists():
                    if root.exists():
                        if incoming.exists():
                            shutil.rmtree(incoming)
                        root.rename(incoming)
                    rollback.rename(root)
                save_settings(info['old_config'])
                _rollback_appearance(info, work)
                info['state'] = 'rolled_back'
                _write_journal(info)
            finally:
                engine.dispose()
        _cleanup_restore(info)
        return True

def restore_backup(file):
    with LOCK:
        recover_pending_restore()
        with tempfile.TemporaryDirectory(dir=HOME) as tmp:
            stage = Path(tmp)
            validate_archive(file, stage)
            old_config = _validated_settings(settings())
            restored_config = _validated_settings(json.loads((stage / 'config.json').read_text(encoding='utf-8')))
            restored_appearance = (_validated_appearance(json.loads((stage / 'appearance.json').read_text(encoding='utf-8')))
                                   if (stage / 'appearance.json').exists() else None)
            for key in ('attachment_dir', 'backup_dir'):
                restored_config[key] = old_config[key]
            attachment_root = directory('attachment_dir')
            _validate_restore_root(attachment_root, old_config)
            safety = create_backup('before_restore')
            token = uuid.uuid4().hex
            info = dict(token=token, state='pending', old_config=old_config,
                        attachment_root=str(attachment_root), database=str(db_path()))
            _, rollback_root, new_root, work = _restore_paths(info)
            work.mkdir()
            journal_written = False
            try:
                shutil.copytree(stage / 'attachments', new_root) if (stage / 'attachments').exists() else new_root.mkdir()
                snapshot(work / 'rollback.db')
                with (work / 'rollback.db').open('r+b') as stream:
                    os.fsync(stream.fileno())
                info['rollback_sha256'] = digest(work / 'rollback.db')
                if restored_appearance is not None:
                    info['appearance'] = _appearance_snapshot(work)
                _write_journal(info)
                journal_written = True
                engine.dispose()
                attachment_root.rename(rollback_root)
                new_root.rename(attachment_root)
                _restore_database(stage / 'personal.db')
                save_settings(restored_config)
                if restored_appearance is not None:
                    save_preferences(restored_appearance, HOME)
                with transaction() as s:
                    s.add(AuditLog(action='恢复备份', entity='database', description='恢复前备份：' + safety.name))
                info['state'] = 'committed'
                _write_journal(info)
            except Exception as exc:
                if journal_written:
                    try:
                        recover_pending_restore()
                    except Exception as recovery_error:
                        raise HTTPException(500, '恢复未完成，回滚资料已保留；请重启程序重试恢复，恢复前备份：' + safety.name) from recovery_error
                    raise HTTPException(500, '恢复失败，原数据库、附件、配置和外观已回滚；恢复前备份：' + safety.name) from exc
                _cleanup_restore(info)
                raise
            finally:
                engine.dispose()
            # Committed recovery is complete even if antivirus temporarily holds a file.
            # Keep the committed journal so startup can finish removing the old files.
            try:
                _cleanup_restore(info)
            except OSError:
                logging.getLogger('personal_database').warning('Restore committed; cleanup deferred to next startup')
            return {'ok': True, 'safety_backup': safety.name}

def integrity():
    with LOCK, closing(sqlite3.connect(db_path())) as conn:
        result = {'integrity': [x[0] for x in conn.execute('PRAGMA integrity_check')],
                  'foreign_keys': conn.execute('PRAGMA foreign_key_check').fetchall(),
                  'missing_attachments': [], 'corrupt_attachments': [], 'orphan_files': [],
                  'unsafe_attachments': [], 'duplicate_uuids': [], 'missing_indexes': []}
        known = set()
        for relative, sha in conn.execute('SELECT file_path,sha256 FROM attachments'):
            if isinstance(relative, str):
                known.add(relative.replace('\\', '/'))
            try:
                path = safe_attachment(relative)
            except HTTPException:
                result['unsafe_attachments'].append(relative)
                continue
            if not path.is_file():
                result['missing_attachments'].append(relative)
            elif digest(path) != sha:
                result['corrupt_attachments'].append(relative)
        base = directory('attachment_dir')
        result['orphan_files'] = [p.relative_to(base).as_posix() for p in base.rglob('*') if p.is_file() and p.relative_to(base).as_posix() not in known]
        indexes = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        for table in Base.metadata.sorted_tables:
            for index in table.indexes:
                if index.name not in indexes:
                    result['missing_indexes'].append(index.name)
            if 'uuid' in table.c:
                result['duplicate_uuids'].extend(conn.execute(f'SELECT uuid FROM "{table.name}" GROUP BY uuid HAVING COUNT(*)>1').fetchall())
        try:
            conn.execute("INSERT INTO entries_fts(entries_fts, rank) VALUES('integrity-check', 1)")
            result['fts'] = 'ok'
        except sqlite3.Error:
            result['fts'] = '全文索引异常'
        result['ok'] = result['integrity'] == ['ok'] and result['fts'] == 'ok' and not any(result[k] for k in ('foreign_keys','missing_attachments','corrupt_attachments','unsafe_attachments','orphan_files','duplicate_uuids','missing_indexes'))
        return result
