from contextlib import contextmanager
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.services import ai_settings
from app.workflow_models import AIConfiguration


@pytest.fixture
def isolated_ai_config(tmp_path, monkeypatch):
    engine = create_engine('sqlite:///:memory:')
    AIConfiguration.__table__.create(engine)

    @contextmanager
    def transaction():
        with Session(engine) as session, session.begin():
            yield session

    monkeypatch.setattr(ai_settings, 'transaction', transaction)
    monkeypatch.setattr(ai_settings, 'SECRET_PATH', tmp_path / 'secrets' / 'key.dpapi')
    yield engine
    engine.dispose()


@pytest.mark.parametrize('value', [
    'http://cloud.example/v1', 'https://name:password@cloud.example/v1',
    'https://cloud.example/v1?key=secret', 'https://cloud.example/v1#part',
    'https://cloud.example/v1?', 'https://cloud.example/v1#',
    'https://cloud.example\\@localhost/v1', 'file:///secret',
])
def test_cloud_endpoint_rejects_unprotected_or_ambiguous_addresses(value):
    with pytest.raises(ValueError):
        ai_settings.validate_base_url('openai_compatible', value)


@pytest.mark.parametrize('value', [
    'http://example.com:11435', 'http://127.0.0.2:11435',
    'http://localhost.example.com:11435', 'http://localhost:11435/api',
    'http://user@127.0.0.1:11435', 'http://localhost:99999',
])
def test_ollama_endpoint_must_be_exact_local_host(value):
    with pytest.raises(ValueError):
        ai_settings.validate_base_url('ollama', value)


def test_canonical_local_and_cloud_urls():
    assert ai_settings.validate_base_url('ollama', 'http://localhost/') == 'http://localhost:11435'
    assert ai_settings.validate_base_url('ollama', 'http://[::1]:11435/') == 'http://[::1]:11435'
    assert ai_settings.validate_base_url('openai_compatible', 'https://cloud.example/v1/') == 'https://cloud.example/v1'


def test_default_is_local_without_automatic_schedule(isolated_ai_config):
    public = ai_settings.public_settings()
    assert public['base_url'] == 'http://127.0.0.1:11435'
    assert public['model'] == 'qwen2.5:1.5b'
    assert public['schedule_enabled'] is False
    assert public['auto_after_save'] is False
    assert public['sensitivityExclude'] is True
    assert public['include_attachments'] is True
    assert public['api_key_set'] is False
    assert 'api_key' not in public


def test_cloud_requires_explicit_consent_before_any_change(isolated_ai_config):
    with pytest.raises(ValueError, match='勾选同意'):
        ai_settings.save_config({'provider': 'openai_compatible', 'base_url': 'https://cloud.example/v1', 'api_key': 'secret'})
    assert not ai_settings.SECRET_PATH.exists()
    assert ai_settings.public_settings()['provider'] == 'ollama'


@pytest.mark.parametrize('payload', [
    {'daily_time': '24:00'}, {'daily_time': '9:00'}, {'daily_time': '12:60'},
    {'schedule_enabled': 'true'}, {'model': '../other'}, {'api_key_set': True},
])
def test_invalid_settings_never_update_preferences(isolated_ai_config, payload):
    with pytest.raises(ValueError):
        ai_settings.save_config(payload)
    assert ai_settings.public_settings()['daily_time'] == '21:00'


@pytest.mark.parametrize('model', ['qwen3:cloud', 'gpt-oss:120b-cloud', 'qwen:cloud-large'])
def test_local_mode_and_pull_never_accept_cloud_models(isolated_ai_config, model):
    from app.services.local_ai import validate_model
    with pytest.raises(ValueError, match='云端模型'):
        validate_model(model)
    with pytest.raises(ValueError, match='云端模型'):
        ai_settings.save_config({'model': model})
    assert validate_model(model, local=False) == model


@pytest.mark.skipif(os.name != 'nt', reason='Windows DPAPI secret storage')
def test_key_is_account_protected_separate_from_db_and_public(isolated_ai_config):
    secret = 'sk-unit-test-never-send'
    result = ai_settings.save_config({'provider': 'openai_compatible',
                                     'base_url': 'https://cloud.example/v1',
                                     'cloud_consent': True, 'api_key': secret})
    assert result['api_key_set'] is True
    assert 'api_key' not in result
    assert secret.encode() not in ai_settings.SECRET_PATH.read_bytes()
    assert ai_settings.get_config()['api_key'] == secret
    with Session(isolated_ai_config) as session:
        row = session.get(AIConfiguration, 1)
        assert 'api_key' not in row.values
        assert secret not in str(row.values)
    # Empty form fields retain a previously saved key.
    ai_settings.save_config({'daily_time': '20:30', 'api_key': ''})
    assert ai_settings.get_config()['api_key'] == secret
    # Unrelated corruption does not make the public settings expose/decrypt secrets.
    ai_settings.SECRET_PATH.write_bytes(b'invalid')
    assert ai_settings.public_settings()['api_key_set'] is True
    with pytest.raises(RuntimeError):
        ai_settings.get_config()
