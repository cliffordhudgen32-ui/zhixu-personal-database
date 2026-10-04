"""Desktop appearance uses shared local preferences without starting a service."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import desktop_control as desktop


def test_desktop_preferences_use_its_configured_home(tmp_path):
    first = desktop.load_config(tmp_path / 'first', {'APP_PORT': '8123'})
    second = desktop.load_config(tmp_path / 'second', {'APP_PORT': '8124'})
    saved = desktop.save_appearance_preferences(first,
        {'skin': 'maple-dark', 'illustrations': False, 'motion': True})
    assert desktop.appearance_preferences(first) == saved
    assert desktop.appearance_preferences(second) == {
        'skin': 'classic', 'illustrations': False, 'motion': False}
    assert (first.home / 'appearance.json').exists()
    assert not (second.home / 'appearance.json').exists()


def test_artwork_stays_with_fixed_local_assets(tmp_path):
    config = desktop.load_config(tmp_path, {})
    # The portable source includes only generic artwork, with no desktop photos.
    assert desktop.skin_art_path(config, 'maple-light') is None
    assert desktop.skin_art_path(config, 'maple-dark') is None
    assert desktop.skin_art_path(config, '../../private') is None
    assert desktop.skin_art_path(config, 'classic') is None
    assert desktop.skin_art_path(config, 'maple-dark', illustrations=False) is None


def test_importing_desktop_does_not_load_application_config_or_gui(tmp_path):
    environment = os.environ | {'PLD_HOME': str(tmp_path), 'PYTHONUTF8': '1'}
    command = [sys.executable, '-c',
        "import sys; from scripts import desktop_control; "
        "print('app.config' in sys.modules, 'tkinter' in sys.modules)"]
    result = subprocess.run(command, cwd=desktop.ROOT, env=environment,
                            capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'False False'
    assert not list(tmp_path.iterdir())


@pytest.mark.skipif(os.name != 'nt' and not os.environ.get('DISPLAY'), reason='Tk display is unavailable')
def test_all_desktop_skins_construct_without_starting_or_writing_database(tmp_path):
    environment = os.environ | {'PLD_HOME': str(tmp_path), 'PYTHONUTF8': '1',
        'DATABASE_URL': 'sqlite:///' + (tmp_path / 'data/personal.db').as_posix()}
    result = subprocess.run([sys.executable, str(desktop.ROOT / 'scripts/desktop_control.py'), '--check-ui'],
        cwd=desktop.ROOT, env=environment, capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stderr
    assert not result.stderr, result.stderr
    report = json.loads(result.stdout)
    assert [row['skin'] for row in report['skins']] == ['maple-light', 'maple-dark', 'classic']
    assert [row['art_loaded'] for row in report['skins']] == [False, False, False]
    for row in report['skins']:
        assert row['footer_bottom'] <= row['height'] - 20
        assert len(row['buttons']) == 19
        assert min(button['height'] for button in row['buttons']) >= 40
    assert report['minimum']['width'] == 1020
    assert report['minimum']['height'] == 780
    assert report['minimum']['footer_bottom'] <= 760
    assert report['minimum']['buttons_right'] <= 1000
    assert not list(tmp_path.iterdir())
