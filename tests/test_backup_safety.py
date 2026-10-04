import hashlib
import json
import sqlite3
import stat
import zipfile
from contextlib import closing
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.services import backup
from app.config import settings
from app.services.appearance import read_preferences, save_preferences


def repack(source, target, changes=None, extra=None, manifest=None):
    with zipfile.ZipFile(source) as archive:
        files = {name: archive.read(name) for name in archive.namelist() if name != 'manifest.json'}
    files.update(changes or {})
    files.update(extra or {})
    if manifest is None:
        manifest = {'format': 'pld-backup', 'version': 1,
                    'sha256': {name: hashlib.sha256(body).hexdigest() for name, body in files.items()}}
    with zipfile.ZipFile(target, 'w') as archive:
        for name, body in files.items():
            archive.writestr(name, body)
        archive.writestr('manifest.json', json.dumps(manifest))
    return target


@pytest.fixture
def saved_state(client, create):
    entry = create(title='备份安全测试', content='已保存的版本')
    attachment = client.post('/api/entries/' + entry['uuid'] + '/attachments',
                             files={'file': ('safety.txt', b'original-attachment')}).json()
    archive = backup.create_backup()
    return entry, attachment, archive


def change_entry(client, entry):
    from app.schemas import EntryInput
    values = {key: value for key, value in entry.items() if key in EntryInput.model_fields}
    values.update(content='恢复前的当前版本', tags=[], projects=[], people=[])
    result = client.put('/api/entries/' + entry['uuid'], json=values)
    assert result.status_code == 200, result.text


def assert_current(client, entry, attachment):
    assert client.get('/api/entries/' + entry['uuid']).json()['content'] == '恢复前的当前版本'
    assert client.get('/api/attachments/' + attachment['uuid']).content == b'original-attachment'


@pytest.mark.parametrize('name', ['../outside', '/absolute', 'attachments/../escape',
                                   'attachments/CON.txt', 'attachments/NUL', 'attachments/com¹.txt',
                                   'attachments/name.', 'attachments/name ', 'attachments/a:b',
                                   'attachments/a\\b', 'attachments/a//b', 'attachments/./b',
                                   'attachments/a\x01b', 'attachments/a?', '.'])
def test_archive_rejects_nonportable_paths(tmp_path, name):
    archive = tmp_path / 'unsafe.zip'
    with zipfile.ZipFile(archive, 'w') as out:
        info = zipfile.ZipInfo(name)
        info.filename = name  # Preserve a hostile Windows separator in the raw ZIP record.
        out.writestr(info, b'attack')
    with pytest.raises(HTTPException) as error:
        backup.validate_archive(archive, tmp_path / 'stage')
    assert error.value.status_code == 400
    assert '不安全路径' in error.value.detail
    assert not (tmp_path / 'outside').exists()


def test_archive_rejects_case_aliases_and_symlinks(tmp_path):
    archive = tmp_path / 'aliases.zip'
    with zipfile.ZipFile(archive, 'w') as out:
        out.writestr('attachments/Example', b'a')
        out.writestr('attachments/example', b'b')
    with pytest.raises(HTTPException) as error:
        backup.validate_archive(archive, tmp_path / 'case-stage')
    assert '重复文件名' in error.value.detail
    info = zipfile.ZipInfo('attachments/link')
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive, 'w') as out:
        out.writestr(info, '../outside')
    with pytest.raises(HTTPException) as error:
        backup.validate_archive(archive, tmp_path / 'link-stage')
    assert '不安全路径' in error.value.detail


@pytest.mark.parametrize('manifest', [None, [], 'invalid', {'format': 'pld-backup', 'version': 1, 'sha256': []},
                                      {'format': 'pld-backup', 'version': True, 'sha256': {}}])
def test_malformed_manifest_is_controlled(saved_state, tmp_path, manifest):
    _, _, source = saved_state
    archive = tmp_path / 'malformed.zip'
    with zipfile.ZipFile(source) as inp:
        bodies = {name: inp.read(name) for name in inp.namelist() if name != 'manifest.json'}
    with zipfile.ZipFile(archive, 'w') as out:
        for name, body in bodies.items():
            out.writestr(name, body)
        out.writestr('manifest.json', json.dumps(manifest))
    with pytest.raises(HTTPException) as error:
        backup.validate_archive(archive, tmp_path / 'stage')
    assert error.value.status_code == 400


def test_invalid_config_rejected_before_restore(client, saved_state, tmp_path):
    entry, attachment, source = saved_state
    change_entry(client, entry)
    invalid = settings() | {'theme': 'malicious-theme'}
    archive = repack(source, tmp_path / 'config.zip', changes={'config.json': json.dumps(invalid).encode()})
    before = {item['name'] for item in backup.list_backups()}
    with pytest.raises(HTTPException) as error:
        backup.restore_backup(archive)
    assert error.value.status_code == 400
    assert {item['name'] for item in backup.list_backups()} == before
    assert_current(client, entry, attachment)
    assert not backup.JOURNAL.exists()


@pytest.mark.parametrize('mutation', ['foreign_key', 'trigger', 'missing_index', 'attachment_size'])
def test_rejects_corrupt_database_relationships_and_schema(saved_state, tmp_path, mutation):
    entry, attachment, source = saved_state
    staged_db = tmp_path / 'mutated.db'
    with zipfile.ZipFile(source) as archive:
        staged_db.write_bytes(archive.read('personal.db'))
    with closing(sqlite3.connect(staged_db)) as conn:
        if mutation == 'foreign_key':
            conn.execute('UPDATE attachments SET entry_id=999999999 WHERE uuid=?', (attachment['uuid'],))
        elif mutation == 'trigger':
            conn.execute('CREATE TRIGGER rogue AFTER INSERT ON audit_logs BEGIN DELETE FROM entries; END')
        elif mutation == 'missing_index':
            conn.execute('DROP INDEX ix_entries_date')
        else:
            conn.execute('UPDATE attachments SET file_size=file_size+1 WHERE uuid=?', (attachment['uuid'],))
        conn.commit()
    malicious = repack(source, tmp_path / 'database.zip', changes={'personal.db': staged_db.read_bytes()})
    with pytest.raises(HTTPException) as error:
        backup.validate_archive(malicious, tmp_path / 'stage')
    assert error.value.status_code == 400


def test_rejects_orphan_attachment_and_missing_required_file(saved_state, tmp_path):
    _, _, source = saved_state
    archive = repack(source, tmp_path / 'orphan.zip', extra={'attachments/orphan': b'unregistered'})
    with pytest.raises(HTTPException) as error:
        backup.validate_archive(archive, tmp_path / 'stage')
    assert '未登记' in error.value.detail
    with zipfile.ZipFile(archive, 'w') as out:
        out.writestr('manifest.json', json.dumps({'format': 'pld-backup', 'version': 1, 'sha256': {}}))
    with pytest.raises(HTTPException) as error:
        backup.validate_archive(archive, tmp_path / 'missing-stage')
    assert '缺少' in error.value.detail
    assert not (tmp_path / 'missing-stage' / 'personal.db').exists()


def test_restore_config_failure_rolls_back_all_state(client, saved_state, monkeypatch):
    entry, attachment, source = saved_state
    change_entry(client, entry)
    config_before = settings()
    original_save = backup.save_settings
    attempts = []

    def fail_once(values):
        attempts.append(values)
        if len(attempts) == 1:
            raise OSError('simulated config write failure')
        return original_save(values)

    monkeypatch.setattr(backup, 'save_settings', fail_once)
    with pytest.raises(HTTPException) as error:
        backup.restore_backup(source)
    assert error.value.status_code == 500 and '已回滚' in error.value.detail
    assert len(attempts) == 2
    assert settings() == config_before
    assert_current(client, entry, attachment)
    assert not backup.JOURNAL.exists()
    assert not list(backup.HOME.glob('.restore-work-*'))


def test_interrupted_restore_recovers_database_attachments_and_config(client, saved_state, monkeypatch):
    entry, attachment, source = saved_state
    change_entry(client, entry)
    later = client.post('/api/entries/' + entry['uuid'] + '/attachments',
                        files={'file': ('later.txt', b'created-after-snapshot')}).json()
    before_config = settings()
    original_save = backup.save_settings

    def simulate_process_exit(values):
        raise SystemExit('simulated interruption after database replacement')

    monkeypatch.setattr(backup, 'save_settings', simulate_process_exit)
    with pytest.raises(SystemExit):
        backup.restore_backup(source)
    assert backup.JOURNAL.exists()
    assert client.get('/api/entries/' + entry['uuid']).json()['content'] == '已保存的版本'
    monkeypatch.setattr(backup, 'save_settings', original_save)
    assert backup.recover_pending_restore()
    assert_current(client, entry, attachment)
    assert client.get('/api/attachments/' + later['uuid']).content == b'created-after-snapshot'
    assert settings() == before_config
    assert not backup.JOURNAL.exists()
    assert not backup.recover_pending_restore()


def test_rollback_failure_keeps_recovery_files(client, saved_state, monkeypatch):
    entry, attachment, source = saved_state
    change_entry(client, entry)
    original_restore = backup._restore_database

    def fail_restore(path):
        raise OSError('simulated unavailable database')

    monkeypatch.setattr(backup, '_restore_database', fail_restore)
    with pytest.raises(HTTPException) as error:
        backup.restore_backup(source)
    assert '回滚资料已保留' in error.value.detail
    info = json.loads(backup.JOURNAL.read_text(encoding='utf-8'))
    assert info['state'] == 'pending'
    assert (backup.HOME / ('.restore-work-' + info['token']) / 'rollback.db').exists()
    monkeypatch.setattr(backup, '_restore_database', original_restore)
    assert backup.recover_pending_restore()
    assert_current(client, entry, attachment)


def test_committed_restore_cleanup_resumes_without_rollback(client, saved_state, monkeypatch):
    entry, _, source = saved_state
    change_entry(client, entry)
    original_cleanup = backup._cleanup_restore

    def fail_cleanup(info):
        raise OSError('simulated Windows antivirus file lock')

    monkeypatch.setattr(backup, '_cleanup_restore', fail_cleanup)
    assert backup.restore_backup(source)['ok']
    assert json.loads(backup.JOURNAL.read_text(encoding='utf-8'))['state'] == 'committed'
    monkeypatch.setattr(backup, '_cleanup_restore', original_cleanup)
    assert backup.recover_pending_restore()
    assert client.get('/api/entries/' + entry['uuid']).json()['content'] == '已保存的版本'
    assert not backup.JOURNAL.exists()


def test_snapshot_closes_sqlite_files(client, tmp_path):
    target = tmp_path / 'closed.db'
    backup.snapshot(target)
    target.unlink()
    assert not target.exists()


def test_directory_swap_failure_restores_original_directory(client, saved_state, monkeypatch):
    entry, attachment, source = saved_state
    change_entry(client, entry)
    original_rename = Path.rename
    failed = []

    def fail_install(path, target):
        if '.restore-' in path.name and not failed:
            failed.append(True)
            raise PermissionError('simulated Windows directory lock')
        return original_rename(path, target)

    monkeypatch.setattr(Path, 'rename', fail_install)
    with pytest.raises(HTTPException) as error:
        backup.restore_backup(source)
    assert '已回滚' in error.value.detail
    assert_current(client, entry, attachment)
    assert not backup.JOURNAL.exists()


def test_interruption_between_directory_renames_is_recoverable(client, saved_state, monkeypatch):
    entry, attachment, source = saved_state
    change_entry(client, entry)
    original_rename = Path.rename

    def interrupt_install(path, target):
        if '.restore-' in path.name:
            raise SystemExit('simulated exit between directory renames')
        return original_rename(path, target)

    monkeypatch.setattr(Path, 'rename', interrupt_install)
    with pytest.raises(SystemExit):
        backup.restore_backup(source)
    assert backup.JOURNAL.exists()
    monkeypatch.setattr(Path, 'rename', original_rename)
    assert backup.recover_pending_restore()
    assert_current(client, entry, attachment)


@pytest.mark.parametrize('phase', ['hashing', 'writing'])
def test_backup_detects_attachment_change_during_zip_write(saved_state, monkeypatch, phase):
    _, attachment, _ = saved_state
    attachment_path = backup.safe_attachment(attachment['file_path'])
    original_body = attachment_path.read_bytes()
    original_info = zipfile.ZipInfo.from_file
    original_digest = backup.digest
    before = {item['name'] for item in backup.list_backups()}

    def modify_before_copy(path, arcname=None, **options):
        if arcname and arcname.startswith('attachments/') and Path(path) == attachment_path:
            attachment_path.write_bytes(b'externally-changed-during-backup')
        return original_info(path, arcname, **options)

    def modify_after_hash(path):
        result = original_digest(path)
        if Path(path) == attachment_path:
            attachment_path.write_bytes(b'externally-changed-after-original-check')
        return result

    if phase == 'writing':
        monkeypatch.setattr(zipfile.ZipInfo, 'from_file', modify_before_copy)
    else:
        monkeypatch.setattr(backup, 'digest', modify_after_hash)
    try:
        with pytest.raises(HTTPException) as error:
            backup.create_backup()
        assert error.value.status_code == 409
        assert '发生变化' in error.value.detail
        assert {item['name'] for item in backup.list_backups()} == before
    finally:
        attachment_path.write_bytes(original_body)


def test_before_upgrade_only_backs_up_existing_old_revision(client, tmp_path, monkeypatch):
    source = tmp_path / 'old.db'
    backup.snapshot(source)
    monkeypatch.setattr(backup, 'db_path', lambda: source)
    reasons = []
    monkeypatch.setattr(backup, 'create_backup', lambda reason: reasons.append(reason) or 'saved')
    assert backup.backup_before_upgrade() is None
    with closing(sqlite3.connect(source)) as conn:
        conn.execute("UPDATE alembic_version SET version_num='old_revision'")
        conn.commit()
    assert backup.backup_before_upgrade() == 'saved'
    assert reasons == ['before_upgrade']


@pytest.fixture
def appearance_state():
    path = backup.HOME / 'appearance.json'
    before = path.read_bytes() if path.exists() else None
    yield path
    if before is None:
        path.unlink(missing_ok=True)
    else:
        path.write_bytes(before)


def test_backup_restores_checked_appearance_with_records(client, saved_state, appearance_state):
    entry, _, _ = saved_state
    expected = {'skin': 'maple-dark', 'illustrations': False, 'motion': True}
    save_preferences(expected, backup.HOME)
    source = backup.create_backup()
    with zipfile.ZipFile(source) as archive:
        body = archive.read('appearance.json')
        assert json.loads(body) == expected
        assert json.loads(archive.read('manifest.json'))['sha256']['appearance.json'] == hashlib.sha256(body).hexdigest()
    change_entry(client, entry)
    save_preferences({'skin': 'classic', 'illustrations': True, 'motion': False}, backup.HOME)
    assert backup.restore_backup(source)['ok']
    assert client.get('/api/entries/' + entry['uuid']).json()['content'] == '已保存的版本'
    assert read_preferences(backup.HOME) == expected


def test_legacy_backup_leaves_current_appearance_unchanged(client, saved_state, tmp_path, appearance_state):
    entry, _, source = saved_state
    legacy = tmp_path / 'legacy.zip'
    with zipfile.ZipFile(source) as archive:
        files = {name: archive.read(name) for name in archive.namelist()
                 if name not in ('manifest.json', 'appearance.json')}
    manifest = {'format': 'pld-backup', 'version': 1,
                'sha256': {name: hashlib.sha256(body).hexdigest() for name, body in files.items()}}
    with zipfile.ZipFile(legacy, 'w') as archive:
        for name, body in files.items():
            archive.writestr(name, body)
        archive.writestr('manifest.json', json.dumps(manifest))
    save_preferences({'skin': 'classic', 'illustrations': False, 'motion': False}, backup.HOME)
    before = appearance_state.read_bytes()
    change_entry(client, entry)
    assert backup.restore_backup(legacy)['ok']
    assert appearance_state.read_bytes() == before


@pytest.mark.parametrize('values', [None, [], {'skin': '../../private.jpg'},
                                   {'skin': 'classic', 'illustrations': 'yes', 'motion': False},
                                   {'skin': 'classic', 'illustrations': True, 'motion': False, 'image': 'secret'}])
def test_invalid_appearance_rejected_before_any_restore(client, saved_state, tmp_path, appearance_state, values):
    entry, attachment, source = saved_state
    change_entry(client, entry)
    archive = repack(source, tmp_path / 'bad-appearance.zip',
                     changes={'appearance.json': json.dumps(values).encode()})
    before = {item['name'] for item in backup.list_backups()}
    with pytest.raises(HTTPException) as error:
        backup.restore_backup(archive)
    assert error.value.status_code == 400
    assert '外观设置无效' in error.value.detail
    assert {item['name'] for item in backup.list_backups()} == before
    assert_current(client, entry, attachment)


@pytest.mark.parametrize('old_body', [None, b'{broken original preference file',
                                   b'{"skin":"classic","illustrations":false,"motion":false}'])
def test_appearance_install_failure_rolls_back_exact_previous_file(
        client, saved_state, monkeypatch, appearance_state, old_body):
    entry, attachment, source = saved_state
    if old_body is None:
        appearance_state.unlink(missing_ok=True)
    else:
        appearance_state.write_bytes(old_body)
    change_entry(client, entry)

    def fail_appearance(*args, **kwargs):
        raise OSError('simulated preference write failure')

    monkeypatch.setattr(backup, 'save_preferences', fail_appearance)
    with pytest.raises(HTTPException) as error:
        backup.restore_backup(source)
    assert '已回滚' in error.value.detail
    assert_current(client, entry, attachment)
    assert (appearance_state.read_bytes() if appearance_state.exists() else None) == old_body
    assert not backup.JOURNAL.exists()


def test_interruption_after_appearance_install_recovers_previous_preferences(
        client, saved_state, monkeypatch, appearance_state):
    entry, attachment, source = saved_state
    old_values = {'skin': 'classic', 'illustrations': False, 'motion': False}
    save_preferences(old_values, backup.HOME)
    before = appearance_state.read_bytes()
    change_entry(client, entry)
    original_write = backup._write_journal

    def interrupt_commit(info):
        if info['state'] == 'committed':
            raise SystemExit('simulated exit after preference install')
        return original_write(info)

    monkeypatch.setattr(backup, '_write_journal', interrupt_commit)
    with pytest.raises(SystemExit):
        backup.restore_backup(source)
    assert json.loads(backup.JOURNAL.read_text(encoding='utf-8'))['state'] == 'pending'
    monkeypatch.setattr(backup, '_write_journal', original_write)
    assert backup.recover_pending_restore()
    assert appearance_state.read_bytes() == before
    assert_current(client, entry, attachment)
    assert not backup.JOURNAL.exists()


def test_damaged_appearance_rollback_keeps_recovery_evidence(
        client, saved_state, monkeypatch, appearance_state):
    _, _, source = saved_state
    save_preferences({'skin': 'classic'}, backup.HOME)
    original_save = backup.save_preferences

    def interrupt_install(*args, **kwargs):
        raise SystemExit('simulated exit during preference install')

    monkeypatch.setattr(backup, 'save_preferences', interrupt_install)
    with pytest.raises(SystemExit):
        backup.restore_backup(source)
    info = json.loads(backup.JOURNAL.read_text(encoding='utf-8'))
    previous = backup.HOME / ('.restore-work-' + info['token']) / 'rollback-appearance.json'
    original_bytes = previous.read_bytes()
    previous.write_bytes(b'tampered')
    monkeypatch.setattr(backup, 'save_preferences', original_save)
    with pytest.raises(RuntimeError, match='外观回滚快照'):
        backup.recover_pending_restore()
    assert backup.JOURNAL.exists() and previous.exists()
    previous.write_bytes(original_bytes)
    assert backup.recover_pending_restore()
