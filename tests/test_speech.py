from contextlib import closing
import io
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import threading
from types import SimpleNamespace
import wave

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from app.services import backup, speech
from app.speech_routes import router


@pytest.fixture
def isolated_speech(tmp_path, monkeypatch):
    monkeypatch.setattr(speech, 'HOME', tmp_path)
    monkeypatch.setattr(speech, '_OPERATION', threading.Lock())
    monkeypatch.setattr(speech, '_STATE', {'phase': 'idle', 'model': None, 'error': ''})
    monkeypatch.setattr(speech, '_MODEL', None)
    monkeypatch.setattr(speech, '_MODEL_NAME', None)
    return tmp_path


def ready_model(home, model='base'):
    directory = home / 'models/speech' / model
    directory.mkdir(parents=True, exist_ok=True)
    for name in speech.REQUIRED_FILES:
        (directory / name).write_bytes(b'fake-model-file-for-unit-tests')
    return directory


@pytest.fixture
def speech_client():
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        yield client


def test_status_is_read_only_and_missing_dependencies_are_explicit(isolated_speech, monkeypatch, speech_client):
    monkeypatch.setattr(speech, 'dependency_status', lambda: {'available': False, 'missing': ['faster_whisper', 'av']})
    response = speech_client.get('/api/speech/status')
    assert response.status_code == 200 and not response.json()['dependencies']['available']
    assert not list(isolated_speech.iterdir())
    response = speech_client.post('/api/speech/prepare', json={'model': 'base'})
    assert response.status_code == 503 and 'requirements-speech.txt' in response.json()['detail']
    assert not list(isolated_speech.iterdir())


def test_transcribe_does_not_prepare_missing_model(isolated_speech, monkeypatch, speech_client):
    monkeypatch.setattr(speech, 'dependency_status', lambda: {'available': True, 'missing': []})
    monkeypatch.setattr(speech, '_download_model', lambda *args: pytest.fail('transcription must not download'))
    response = speech_client.post('/api/speech/transcribe', files={'file': ('voice.wav', b'fake-audio')})
    assert response.status_code == 409 and '先点击准备' in response.json()['detail']
    assert not list(isolated_speech.iterdir())


@pytest.mark.parametrize('filename,body,status', [('voice.wav', b'', 400), ('voice.txt', b'body', 400),
                                                ('voice.webm', b'a' * 101, 413)])
def test_upload_size_and_type_rejected_before_model(isolated_speech, monkeypatch, speech_client, filename, body, status):
    monkeypatch.setattr(speech, 'MAX_BYTES', 100)
    monkeypatch.setattr(speech, '_load_model', lambda *args: pytest.fail('invalid upload must not load model'))
    response = speech_client.post('/api/speech/transcribe', files={'file': (filename, body)})
    assert response.status_code == status
    assert not list(isolated_speech.iterdir())


def test_preparation_only_on_explicit_request_and_staged_atomically(isolated_speech, monkeypatch, speech_client):
    monkeypatch.setattr(speech, 'dependency_status', lambda: {'available': True, 'missing': []})
    calls = []

    def download(model, stage):
        calls.append(model)
        assert stage.name.startswith('.prepare-base-')
        assert not (isolated_speech / 'models/speech/base').exists()
        for name in speech.REQUIRED_FILES:
            (stage / name).write_bytes(b'fake-download')

    class ImmediateThread:
        def __init__(self, target, args, **kwargs):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(speech, '_download_model', download)
    monkeypatch.setattr(speech.threading, 'Thread', ImmediateThread)
    assert speech_client.get('/api/speech/status').json()['phase'] == 'idle'
    assert calls == []
    result = speech_client.post('/api/speech/prepare', json={'model': 'base'})
    assert result.status_code == 200 and result.json()['phase'] == 'ready'
    assert calls == ['base'] and speech._ready('base')
    assert not list((isolated_speech / 'models/speech').glob('.prepare-*'))
    assert speech_client.post('/api/speech/prepare', json={'model': 'base'}).status_code == 200
    assert calls == ['base']


def test_failed_download_does_not_activate_partial_model(isolated_speech, monkeypatch):
    def interrupted(model, stage):
        (stage / 'model.bin').write_bytes(b'incomplete')
        raise OSError('download interrupted')

    monkeypatch.setattr(speech, '_download_model', interrupted)
    speech._OPERATION.acquire()
    speech._prepare_worker('base')
    assert speech.status()['phase'] == 'failed'
    assert not speech._ready('base')
    assert not list((isolated_speech / 'models/speech').glob('.prepare-*'))
    assert speech._OPERATION.acquire(blocking=False)
    speech._OPERATION.release()


def test_model_loader_uses_complete_local_path_and_offline_cpu(isolated_speech, monkeypatch):
    directory = ready_model(isolated_speech)
    calls = []

    def constructor(path, **kwargs):
        calls.append((path, kwargs))
        return SimpleNamespace()

    monkeypatch.setitem(sys.modules, 'faster_whisper', SimpleNamespace(WhisperModel=constructor))
    assert speech._load_model('base') is speech._load_model('base')
    assert len(calls) == 1 and calls[0][0] == str(directory)
    assert calls[0][1]['local_files_only'] is True
    assert calls[0][1]['device'] == 'cpu' and calls[0][1]['compute_type'] == 'int8'
    (directory / 'tokenizer.json').unlink()
    with pytest.raises(HTTPException) as error:
        speech._load_model('base')
    assert error.value.status_code == 409 and len(calls) == 1


def test_download_uses_fixed_revision_and_no_implicit_hf_token(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, 'faster_whisper.utils',
                        SimpleNamespace(download_model=lambda *args, **kwargs: calls.append((args, kwargs))))
    speech._download_model('base', tmp_path)
    assert calls[0][0] == ('base',)
    assert calls[0][1]['revision'] == speech.MODEL_REVISIONS['base']
    assert calls[0][1]['use_auth_token'] is False


def test_transcription_returns_unarchived_result_and_does_not_mutate_database(
        isolated_speech, monkeypatch, client, speech_client):
    ready_model(isolated_speech)
    monkeypatch.setattr(speech, 'dependency_status', lambda: {'available': True, 'missing': []})
    monkeypatch.setattr(speech, '_decode_audio', lambda body: ('bounded-samples', 2.5))
    monkeypatch.setattr(speech, '_download_model', lambda *args: pytest.fail('unexpected download'))
    options = []

    class Recognizer:
        def transcribe(self, audio, **kwargs):
            options.append(kwargs)
            assert audio == 'bounded-samples'
            return iter([SimpleNamespace(start=0, end=2.4, text='  这是合成演示记录。  ')]), SimpleNamespace(language='zh')

    monkeypatch.setattr(speech, '_load_model', lambda model: Recognizer())
    with closing(sqlite3.connect(backup.db_path())) as conn:
        before = (conn.execute('SELECT COUNT(*) FROM entries').fetchone(),
                  conn.execute('SELECT COUNT(*) FROM attachments').fetchone())
    response = speech_client.post('/api/speech/transcribe', files={'file': ('voice.wav', b'fake-audio')})
    assert response.status_code == 200
    result = response.json()
    assert result['text'] == '这是合成演示记录。' and result['saved'] is False
    assert result['language'] == 'zh' and result['duration'] == 2.5
    assert options[0]['language'] == 'zh' and options[0]['vad_filter'] is True
    with closing(sqlite3.connect(backup.db_path())) as conn:
        after = (conn.execute('SELECT COUNT(*) FROM entries').fetchone(),
                 conn.execute('SELECT COUNT(*) FROM attachments').fetchone())
    assert before == after


def test_audio_duration_is_checked_before_recognizer(isolated_speech, monkeypatch):
    ready_model(isolated_speech)
    monkeypatch.setattr(speech, 'dependency_status', lambda: {'available': True, 'missing': []})
    monkeypatch.setattr(speech, '_decode_audio', lambda body: (None, 601))
    monkeypatch.setattr(speech, '_load_model', lambda model: pytest.fail('too-long audio must not load model'))
    with pytest.raises(HTTPException) as error:
        speech.transcribe(b'audio', 'voice.wav')
    assert error.value.status_code == 413
    assert speech._OPERATION.acquire(blocking=False)
    speech._OPERATION.release()


def test_unknown_duration_is_bounded_by_decoded_samples(monkeypatch):
    class Samples:
        size = speech.MAX_SECONDS * speech.SAMPLE_RATE + 1

        def reshape(self, value):
            return self

    class Frame:
        pts = 0

        def to_ndarray(self):
            return Samples()

    class Container:
        duration = None
        streams = SimpleNamespace(audio=[object()])

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def decode(self, **kwargs):
            return iter([Frame()])

    fake = SimpleNamespace(open=lambda *args, **kwargs: Container(), time_base=1_000_000,
                           audio=SimpleNamespace(resampler=SimpleNamespace(
                               AudioResampler=lambda **kwargs: SimpleNamespace(resample=lambda frame: [frame] if frame else []))))
    monkeypatch.setitem(sys.modules, 'av', fake)
    with pytest.raises(HTTPException) as error:
        speech._decode_audio(b'compressed-audio-without-duration')
    assert error.value.status_code == 413


def test_parallel_operations_and_arbitrary_models_rejected(isolated_speech, monkeypatch, speech_client):
    monkeypatch.setattr(speech, 'dependency_status', lambda: {'available': True, 'missing': []})
    speech._OPERATION.acquire()
    try:
        assert speech_client.post('/api/speech/prepare', json={'model': 'base'}).status_code == 409
        assert speech_client.post('/api/speech/transcribe', files={'file': ('voice.wav', b'audio')}).status_code == 409
    finally:
        speech._OPERATION.release()
    assert speech_client.post('/api/speech/prepare', json={'model': '../../secret'}).status_code == 422
    assert speech_client.post('/api/speech/transcribe', files={'file': ('voice.wav', b'audio')},
                              data={'model': '../../secret'}).status_code == 400


def test_real_short_wav_decoder_when_optional_dependency_installed():
    pytest.importorskip('av')
    target = io.BytesIO()
    with wave.open(target, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b'\0\0' * 8000)
    samples, duration = speech._decode_audio(target.getvalue())
    assert len(samples) == 16000 and duration == 1


def test_frontend_confirmation_no_automatic_microphone_and_retry_without_duplicates(tmp_path):
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node unavailable for frontend unit check')
    probe = tmp_path / 'speech-contract.js'
    probe.write_text(r'''
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const crypto = require('node:crypto').webcrypto;
const nodes = new Map();
const element = id => {
  if (!nodes.has(id)) nodes.set(id, { value:'', checked:false, disabled:false, hidden:false, textContent:'', innerHTML:'' });
  return nodes.get(id);
};
let microphoneCalls = 0, createCalls = 0, attachmentCalls = 0, prepareCalls = 0;
let archived = null, serverAttachment = null;
const context = {
  window: { MediaRecorder: class {}, addEventListener(){} },
  navigator: { mediaDevices: { getUserMedia: async () => { microphoneCalls++; throw Error('unexpected microphone'); } } },
  document: { getElementById: element }, location: {hash:'#speech-capture'},
  URL: {createObjectURL:()=> 'blob:unit-audio', revokeObjectURL(){}},
  crypto, FormData: class {append(){}},
  setTimeout, clearTimeout, setInterval, clearInterval,
  $: selector => element(selector.slice(1)), esc: value => String(value), heading:()=>'', toast(){},
  api: async (path, options={}) => {
    if (path === '/api/speech/status') return {dependencies:{available:true}, models:[{id:'base',ready:true}],phase:'idle'};
    if (path === '/api/speech/transcribe') return {text:'这是机器原始转写。',duration:2,language:'zh',model:'base',engine:'faster-whisper-local'};
    if (path === '/api/speech/prepare') {prepareCalls++; throw Error('unexpected prepare');}
    if (path === '/api/entries') {createCalls++; archived = options.body; return {uuid:options.body.uuid};}
    if (path.endsWith('/attachments')) {
      attachmentCalls++;
      serverAttachment = {sha256:archived.metadata_json.speech.audio_sha256,file_size:4};
      throw Error('simulated response loss after successful attachment save');
    }
    if (path.startsWith('/api/entries/')) return {...archived, attachments:serverAttachment ? [serverAttachment] : []};
    throw Error('unexpected API '+path);
  },
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), context);
(async () => {
  await context.window.speechCapturePage();
  assert.equal(microphoneCalls,0); assert.equal(prepareCalls,0);
  const file = {name:'synthetic.wav',size:4,arrayBuffer:async()=> new Uint8Array([1,2,3,4]).buffer};
  element('speech-file').onchange({target:{files:[file]}});
  element('speech-text').oninput({target:{value:'这是核对后的人工正文。'}});
  await element('speech-transcribe').onclick();
  assert.equal(context.window.SpeechCaptureUI.state.text,'这是核对后的人工正文。');
  assert.equal(context.window.SpeechCaptureUI.state.original,'这是机器原始转写。');
  await element('speech-save').onclick();
  assert.equal(createCalls,0); assert.equal(attachmentCalls,0);
  element('speech-confirm').checked = true;
  await element('speech-save').onclick();
  assert.equal(createCalls,1); assert.equal(attachmentCalls,1);
  assert.equal(context.window.SpeechCaptureUI.state.file,file);
  assert.ok(context.window.SpeechCaptureUI.state.savedUUID);
  assert.equal(archived.item_type,'inbox');
  assert.equal(archived.metadata_json.speech.original_transcript,'这是机器原始转写。');
  await element('speech-save').onclick();
  assert.equal(createCalls,1); assert.equal(attachmentCalls,1);
  assert.equal(context.window.SpeechCaptureUI.state.file,null);
  assert.equal(microphoneCalls,0); assert.equal(prepareCalls,0);
})().catch(error=>{ console.error(error);process.exitCode=1; });
''', encoding='utf-8')
    script = Path(__file__).resolve().parents[1] / 'app/static/speech.js'
    result = subprocess.run([node, str(probe), str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
