"""Shared appearance storage must stay separate from records and remain atomic."""
import json
from pathlib import Path

import pytest
from app.services import appearance


def test_desktop_and_web_share_disk_preferences_without_touching_data(tmp_path, monkeypatch, client):
    config = tmp_path / 'config.json'
    config.write_text('{"name":"原有数据库配置"}', encoding='utf-8')
    original_config = config.read_bytes()
    monkeypatch.setattr(appearance, '_preference_path', lambda home=None: tmp_path / 'appearance.json')
    # The desktop can set preferences without starting the server.
    appearance.save_preferences({'skin': 'maple-dark', 'illustrations': False}, home=tmp_path)
    assert client.get('/api/appearance').json() == dict(skin='maple-dark', illustrations=False, motion=False)
    response = client.put('/api/appearance', json={'skin': 'maple-light', 'motion': True})
    assert response.status_code == 200
    # A fresh desktop read sees the browser's update, retaining omitted fields.
    assert appearance.read_preferences(home=tmp_path) == dict(skin='maple-light', illustrations=False, motion=True)
    assert config.read_bytes() == original_config
    assert json.loads((tmp_path / 'appearance.json').read_text(encoding='utf-8')) == response.json()


def test_failed_replace_keeps_previous_skin_and_cleans_its_temporary_file(tmp_path, monkeypatch):
    appearance.save_preferences({'skin': 'classic'}, home=tmp_path)
    before = (tmp_path / 'appearance.json').read_bytes()

    def fail_replace(*args):
        raise PermissionError('Synthetic locked preferences file')

    monkeypatch.setattr(appearance.os, 'replace', fail_replace)
    with pytest.raises(PermissionError):
        appearance.save_preferences({'skin': 'maple-dark'}, home=tmp_path)
    assert (tmp_path / 'appearance.json').read_bytes() == before
    assert not list(tmp_path.glob('.appearance-*.tmp'))


def test_invalid_skin_or_arbitrary_image_path_never_changes_preferences(tmp_path, monkeypatch, client):
    monkeypatch.setattr(appearance, '_preference_path', lambda home=None: tmp_path / 'appearance.json')
    for payload in ({'skin': '../../private'}, {'image_path': 'D:/private.jpg'}, {'motion': 'false'}):
        response = client.put('/api/appearance', json=payload)
        assert response.status_code == 400
    assert not (tmp_path / 'appearance.json').exists()
