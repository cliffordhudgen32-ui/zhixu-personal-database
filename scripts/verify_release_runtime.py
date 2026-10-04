"""Validate a built candidate with an empty temporary home, never the live data."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile

import httpx


def verify(package: Path):
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='zhixu-release-') as temporary:
        workspace = Path(temporary) / 'program'
        home = Path(temporary) / 'empty-home'
        home.mkdir()
        (home / 'logs').mkdir()
        with zipfile.ZipFile(package) as archive:
            names = set(archive.namelist())
            manifest = json.loads(archive.read('release-manifest.json'))
            assert set(manifest['sha256']) == names - {'release-manifest.json'}
            for name, expected in manifest['sha256'].items():
                assert hashlib.sha256(archive.read(name)).hexdigest() == expected, name
                assert not Path(name).is_absolute() and '..' not in Path(name).parts
            required = {'scripts/quick_capture.py', 'scripts/desktop_tray.py',
                        'scripts/desktop_clipboard.py', 'scripts/desktop_hotkey.py',
                        'app/capture_models.py', 'app/capture_routes.py', 'app/services/capture.py',
                        'app/action_routes.py', 'app/services/action_followup.py',
                        'app/static/capture-workspace.js', 'app/static/capture-workspace.css',
                        'app/static/ai-followups.js', 'app/static/ai-followups.css',
                        'database/versions/20261004_capture.py',
                        'app/services/speech.py', 'app/speech_routes.py', 'requirements-speech.txt',
                        'app/services/knowledge_answer.py', 'app/answer_routes.py',
                        'app/services/review_assistance.py', 'app/services/period_review.py',
                        'app/static/ai-review-tools.js', 'app/static/knowledge-answer.js',
                        'app/static/period-review.js', 'app/static/speech.js'}
            assert required <= names
            assert not any(name.endswith(('.jpg', '.jpeg', '.db', '.onnx', '.gguf')) or
                           name.startswith(('data/', 'models/', 'logs/', 'backups/', '.venv/', '.git/'))
                           or '/screenshots/' in name for name in names)
            assert '.env' not in names and 'ai-settings.json' not in names and 'config.json' not in names
            assert json.loads(archive.read('appearance.json')) == {
                'skin': 'classic', 'illustrations': False, 'motion': False}
            archive.extractall(workspace)
        for path in workspace.rglob('*.py'):
            compile(path.read_bytes(), str(path), 'exec')
        environment = os.environ | {'PLD_HOME': str(home), 'PYTHONUTF8': '1',
            'DATABASE_URL': 'sqlite:///' + (home / 'data/personal.db').as_posix(),
            'APP_PORT': '8012', 'PLD_WORKERS_DISABLED': '1'}
        imported = subprocess.run([sys.executable, '-c',
            'from scripts import desktop_control, quick_capture, desktop_tray; print("imports OK")'],
            cwd=workspace, env=environment, capture_output=True, text=True, encoding='utf-8', timeout=20)
        assert imported.returncode == 0, imported.stderr
        marker = home / ('logs/.desktop-stop-' + uuid.uuid4().hex)
        with (home / 'server.log').open('wb') as log:
            process = subprocess.Popen([sys.executable, str(workspace / 'scripts/desktop_control.py'),
                '--serve', '--port', '8012', '--stop-file', str(marker)], cwd=workspace, env=environment,
                stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            try:
                with httpx.Client(base_url='http://127.0.0.1:8012', timeout=10) as client:
                    for _ in range(150):
                        assert process.poll() is None, (home / 'server.log').read_text(errors='replace')
                        try:
                            session = client.get('/api/session')
                            if session.status_code == 200:
                                break
                        except httpx.ConnectError:
                            pass
                        time.sleep(.2)
                    else:
                        raise AssertionError('candidate service did not start')
                    client.headers['x-local-token'] = session.json()['token']
                    assert not session.json()['settings']['setup_done']
                    html = client.get('/').text
                    assets = set(re.findall(r'(?:href|src)="(/static/[^\"]+)"', html))
                    for asset in assets:
                        response = client.get(asset)
                        assert response.status_code == 200, asset
                    appearance = client.get('/api/appearance').json()
                    assert appearance['skin'] == 'classic' and not appearance['illustrations']
                    ai = client.get('/api/ai/settings').json()
                    assert not ai['weekly_enabled'] and not ai['monthly_enabled']
                    assert ai['provider'] == 'ollama' and not ai['cloud_consent']
                    assert not ai['schedule_enabled'] and not ai['auto_after_save']
                    speech = client.get('/api/speech/status').json()
                    assert all(not model['ready'] for model in speech['models'])
                    answer = client.post('/api/knowledge/answer', json={'question': '演示问题'})
                    assert answer.status_code == 200 and not answer.json()['answerable']
                    assert client.post('/api/ai/period', json={'kind': 'invalid', 'date': '2026-10-04'}).status_code == 422
                    assert client.post('/api/speech/prepare', json={'model': 'invalid'}).status_code == 422
                    assert client.get('/api/capture-drafts').json()['total'] == 0
                    assert client.get('/api/capture-drafts?status=discarded').json()['total'] == 0
                    with closing(sqlite3.connect(home / 'data/personal.db')) as conn:
                        assert conn.execute('SELECT COUNT(*) FROM entries').fetchone()[0] == 0
            finally:
                marker.touch()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=10)
            assert process.returncode == 0
            with httpx.Client(timeout=2) as client:
                try:
                    client.get('http://127.0.0.1:8012/api/session')
                except (httpx.ConnectError, httpx.ConnectTimeout):
                    pass
                else:
                    raise AssertionError('candidate listener remains')
        result = {'version': manifest['version'], 'files': len(names),
                  'sha256': hashlib.sha256(package.read_bytes()).hexdigest(),
                  'manifest_hashes_verified': len(manifest['sha256']),
                  'static_resources_served': len(assets), 'empty_home_startup': 'passed',
                  'desktop_module_imports': 'passed', 'private_files_excluded': True,
                  'automatic_drafts_disabled_by_default': True, 'weekly_monthly_disabled_by_default': True,
                  'own_service_stopped': True}
    destination = root / 'dist' / ('zhixu-' + manifest['version'] + '-verification.json')
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    verify(Path(sys.argv[1]).resolve())
