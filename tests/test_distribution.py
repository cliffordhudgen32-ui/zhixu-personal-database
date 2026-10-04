import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from scripts import build_release


@pytest.fixture
def source_tree(tmp_path):
    root = tmp_path / 'source'
    for relative in build_release.NAMED_FILES:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'public program resource\n')
    for relative in build_release.PROGRAM_DIRECTORIES:
        (root / relative).mkdir(parents=True, exist_ok=True)
    (root / 'app/services/appearance.py').write_text(
        "DEFAULTS = {'skin': 'maple-light', 'illustrations': True, 'motion': False}\n", encoding='utf-8')
    (root / 'app/static/appearance.js').write_text(
        'const defaults = { skin: "maple-light", illustrations: true, motion: false };\n'
        + '\n'.join('/static/skins/' + name for name in build_release.USER_ART), encoding='utf-8')
    (root / 'scripts/desktop_control.py').write_text(
        "SKINS = {'maple-light': {'art': 'maple-reading.jpg'}, 'maple-dark': {'art': 'maple-night.jpg'}}\n",
        encoding='utf-8')
    (root / 'app/main.py').write_bytes(b'# public module\n')
    return root


def test_release_allowlist_excludes_private_files_and_reference_images(source_tree, tmp_path):
    private = b'PRIVATE-CANARY-never-distribute-this'
    names = ['.env', 'config.json', 'ai-settings.json', 'README.md',
             'data/personal.db', 'data/attachments/private.txt', 'models/private.gguf',
             'logs/app.log', 'backups/private.zip', 'exports/private.md',
             'secrets/api.key', '.venv/python.exe', '.git/config',
             'tests/fixtures/private.txt', 'docs/screenshots/private.jpg',
             'app/static/private.js', 'app/static/private.json',
             'app/services/.env', 'app/services/data/private.py',
             'database/versions/__pycache__/private.pyc']
    names += ['app/static/skins/' + image for image in build_release.USER_ART]
    for relative in names:
        path = source_tree / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(private)
    optional = source_tree / 'scripts/quick_capture.py'
    optional.write_bytes(b'# optional quick capture module\n')
    output = tmp_path / 'preview.zip'
    result = build_release.build_release(output, '0.1.0-test', source_tree)
    assert result['publication'] == 'not-published'
    with zipfile.ZipFile(output) as archive:
        actual = archive.namelist()
        assert 'scripts/quick_capture.py' in actual
        assert not set(names) - {'README.md'} & set(actual)
        assert 'app/static/skins/maple-placeholder.svg' in actual
        assert all(private not in archive.read(name) for name in actual)
        assert json.loads(archive.read('appearance.json')) == build_release.SAFE_APPEARANCE
        assert b"'skin': 'classic'" in archive.read('app/services/appearance.py')
        for name in ('app/static/appearance.js', 'app/static/appearance.css', 'scripts/desktop_control.py'):
            assert all(image.encode() not in archive.read(name) for image in build_release.USER_ART)
        manifest = json.loads(archive.read('release-manifest.json'))
        assert manifest['publication'] == 'not-published' and manifest['license_review'] == 'pending'
        assert set(manifest['sha256']) == set(actual) - {'release-manifest.json'}
        assert all(hashlib.sha256(archive.read(name)).hexdigest() == expected
                   for name, expected in manifest['sha256'].items())


def test_build_does_not_overwrite_existing_file_or_leave_temp(source_tree, tmp_path):
    output = tmp_path / 'existing.zip'
    output.write_bytes(b'keep-existing-build')
    with pytest.raises(ValueError, match='不覆盖'):
        build_release.build_release(output, source_root=source_tree)
    assert output.read_bytes() == b'keep-existing-build'
    assert not list(tmp_path.glob('.release-*.tmp'))


def test_build_rejects_source_link_outside_program_root(source_tree, tmp_path):
    target = tmp_path / 'secret-main.py'
    target.write_bytes(b'private source')
    module = source_tree / 'app/main.py'
    module.unlink()
    try:
        module.symlink_to(target)
    except OSError:
        pytest.skip('Windows account cannot create symbolic links')
    with pytest.raises(ValueError, match='不安全'):
        build_release.build_release(tmp_path / 'unsafe.zip', source_root=source_tree)
    assert not (tmp_path / 'unsafe.zip').exists()


def test_actual_release_contains_program_resources_and_generic_defaults(tmp_path):
    output = tmp_path / 'real-preview.zip'
    build_release.build_release(output)
    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
        assert 'app/main.py' in names and 'app/services/backup.py' in names
        assert 'requirements.lock.txt' in names
        assert json.loads(archive.read('appearance.json'))['skin'] == 'classic'
        assert all(not name.endswith(('.jpg', '.jpeg', '.db', '.gguf', '.onnx')) for name in names)
        assert all('/screenshots/' not in name and not name.startswith(('data/', 'models/', '.venv/')) for name in names)
