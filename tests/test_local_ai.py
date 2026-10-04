import json

import httpx
import pytest

from app.services import local_ai


def test_local_generation_is_schema_bounded_and_never_approves(monkeypatch):
    sent = []

    def respond(request):
        if request.url.path == '/api/show':
            assert json.loads(request.content) == {'model': 'qwen2.5:1.5b'}
            return httpx.Response(200, json={'model_info': {'general.architecture': 'qwen2'},
                                             'details': {'format': 'gguf', 'family': 'qwen2'}})
        assert request.url.path == '/api/chat'
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={'message': {'content': '{"title":"虚构草稿"}'}})

    monkeypatch.setattr(local_ai, 'ensure_runtime', lambda config: True)
    monkeypatch.setattr(local_ai, '_client', lambda *args: httpx.Client(transport=httpx.MockTransport(respond)))
    result = local_ai.organize([{'text': '虚构示例'}],
                              {'provider': 'ollama', 'base_url': 'http://127.0.0.1:11435', 'model': 'qwen2.5:1.5b'},
                              {'type': 'object'}, '仅整理示例')
    assert result == {'title': '虚构草稿'}
    assert sent[0]['format'] == {'type': 'object'}
    assert sent[0]['think'] is False
    assert sent[0]['keep_alive'] == 0
    assert sent[0]['options']['num_predict'] == 3000
    assert sent[0]['options']['num_gpu'] == 0
    assert '确认归档' not in json.dumps(sent[0], ensure_ascii=False)


def test_cloud_without_consent_never_opens_network(monkeypatch):
    def forbid(*args, **kwargs):
        raise AssertionError('No network without user consent')

    monkeypatch.setattr(local_ai, '_client', forbid)
    with pytest.raises(ValueError, match='尚未确认'):
        local_ai.organize([{'text': '用户资料'}],
                          {'provider': 'openai_compatible', 'base_url': 'https://cloud.example/v1', 'model': 'model'}, {})


def test_model_response_limit_is_checked_while_streaming():
    class Chunks(httpx.SyncByteStream):
        def __iter__(self):
            yield b'{"message":"'
            yield b'x' * 32
            raise AssertionError('A bounded request must stop before consuming subsequent data')

    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Chunks()))) as client:
        with pytest.raises(ValueError, match='内容过大'):
            local_ai._request_json(client, 'GET', 'http://127.0.0.1:11435/test', maximum=20)


def test_cloud_redirect_does_not_forward_sources_or_key(monkeypatch):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(307, headers={'Location': 'https://other.example/stolen'})

    monkeypatch.setattr(local_ai, '_client', lambda *args: httpx.Client(transport=httpx.MockTransport(respond), follow_redirects=False))
    with pytest.raises(httpx.HTTPStatusError):
        local_ai.organize([{'text': '虚构资料'}],
                          {'provider': 'openai_compatible', 'base_url': 'https://cloud.example/v1',
                           'model': 'model', 'cloud_consent': True, 'api_key': 'fake-unit-key'}, {})
    assert len(requests) == 1
    assert requests[0].url.host == 'cloud.example'
