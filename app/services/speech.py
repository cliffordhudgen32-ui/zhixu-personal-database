"""Optional local ASR. Preparation is explicit; transcription never downloads or archives."""
from __future__ import annotations

import importlib.util
import io
import itertools
import math
from pathlib import Path
import shutil
import tempfile
import threading
import time

from fastapi import HTTPException
from app.config import HOME

MAX_BYTES = 25 * 1024 * 1024
MAX_SECONDS = 600
SAMPLE_RATE = 16_000
MODELS = ('base', 'small')
MODEL_REVISIONS = {'base': 'ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66',
                   'small': '536b0662742c02347bc0e980a01041f333bce120'}
REQUIRED_FILES = ('model.bin', 'config.json', 'tokenizer.json', 'vocabulary.txt')
EXTENSIONS = {'.wav', '.mp3', '.m4a', '.mp4', '.webm', '.ogg', '.flac', '.aac'}
_OPERATION = threading.Lock()
_STATE_LOCK = threading.RLock()
_STATE = {'phase': 'idle', 'model': None, 'error': ''}
_MODEL = None
_MODEL_NAME = None


def _model_name(value):
    if value not in MODELS:
        raise HTTPException(400, '请选择 base 或 small 本机语音模型')
    return value


def _model_directory(model):
    model = _model_name(model)
    # The app's fixed models root may be an administrator-created junction used
    # for a shared local cache. No request can change that root or select a path.
    models_root = (Path(HOME).resolve() / 'models').resolve()
    path = models_root / 'speech' / model
    if not path.resolve().is_relative_to(models_root) or path.is_symlink():
        raise HTTPException(400, '语音模型目录必须位于本机模型目录内')
    return path.resolve()


def _ready(model):
    directory = _model_directory(model)
    return all((directory / name).is_file() and not (directory / name).is_symlink()
               and (directory / name).stat().st_size > 0 for name in REQUIRED_FILES)


def dependency_status():
    missing = []
    for name in ('faster_whisper', 'av', 'numpy'):
        try:
            available = importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            available = False
        if not available:
            missing.append(name)
    return {'available': not missing, 'missing': missing}


def _require_dependencies():
    if not dependency_status()['available']:
        raise HTTPException(503, '尚未安装本机语音组件；请安装 requirements-speech.txt 后重新启动数据库')


def status():
    with _STATE_LOCK:
        progress = dict(_STATE)
    states = []
    for model in MODELS:
        try:
            ready = _ready(model)
        except (OSError, HTTPException):
            ready = False
        states.append({'id': model, 'ready': ready})
    return {'dependencies': dependency_status(), 'models': states, **progress,
            'max_bytes': MAX_BYTES, 'max_seconds': MAX_SECONDS,
            'privacy': '音频仅提交给本机转写；不会发送云端，确认保存前不写入资料库'}


def _set_state(**values):
    with _STATE_LOCK:
        _STATE.update(values)


def _download_model(model, directory):
    # The official library maps these two fixed names to Systran repositories.
    from faster_whisper.utils import download_model
    return download_model(model, output_dir=str(directory), local_files_only=False,
                          use_auth_token=False, revision=MODEL_REVISIONS[model])


def _prepare_worker(model):
    try:
        target = _model_directory(model)
        target.parent.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(target.parent).free < 1024 ** 3:
            raise HTTPException(400, '语音模型准备至少需要 1 GB 可用空间')
        if target.exists():
            if _ready(model):
                _set_state(phase='ready', model=model, error='')
                return
            raise HTTPException(409, '已有语音模型目录不完整，请保留目录并检查后再准备')
        # Download into an owned sibling stage; a partial download is never ready.
        with tempfile.TemporaryDirectory(prefix='.prepare-' + model + '-', dir=target.parent) as temporary:
            stage = Path(temporary)
            _download_model(model, stage)
            if not all((stage / name).is_file() and not (stage / name).is_symlink()
                       and (stage / name).stat().st_size > 0 for name in REQUIRED_FILES):
                raise HTTPException(400, '下载的语音模型不完整，未启用')
            if target.exists():
                raise HTTPException(409, '准备期间模型目录已存在，请重新检查状态')
            stage.rename(target)
        _set_state(phase='ready', model=model, error='')
    except Exception as exc:
        detail = exc.detail if isinstance(exc, HTTPException) else '模型下载失败，请检查网络、空间与语音依赖后重试'
        _set_state(phase='failed', model=model, error=detail)
    finally:
        _OPERATION.release()


def prepare(model='base'):
    model = _model_name(model)
    _require_dependencies()
    if _ready(model):
        return status()
    if not _OPERATION.acquire(blocking=False):
        raise HTTPException(409, '已有语音准备或转写任务正在处理，请稍后重试')
    _set_state(phase='preparing', model=model, error='')
    try:
        threading.Thread(target=_prepare_worker, args=(model,), daemon=True,
                         name='local-speech-prepare').start()
    except Exception:
        _OPERATION.release()
        _set_state(phase='failed', model=model, error='无法启动语音准备任务')
        raise
    return status()


def validate_upload(body, filename):
    if not body:
        raise HTTPException(400, '请选择有内容的录音文件')
    if len(body) > MAX_BYTES:
        raise HTTPException(413, '录音文件不能超过 25 MB，请分段处理')
    if not isinstance(filename, str) or Path(filename).suffix.lower() not in EXTENSIONS:
        raise HTTPException(400, '支持 WAV、MP3、M4A、MP4、WebM、OGG、FLAC 和 AAC 录音')


def _decode_audio(body):
    """Decode bounded samples rather than trusting a compressed file's metadata."""
    import av
    import numpy as np

    buffer = io.BytesIO()
    count = 0
    started = time.monotonic()
    try:
        with av.open(io.BytesIO(body), mode='r', metadata_errors='ignore') as container:
            if not container.streams.audio:
                raise HTTPException(400, '文件中没有可识别的音频')
            if container.duration is not None and container.duration / av.time_base > MAX_SECONDS + 0.01:
                raise HTTPException(413, '录音不能超过 10 分钟，请分段处理')
            resampler = av.audio.resampler.AudioResampler(format='s16', layout='mono', rate=SAMPLE_RATE)
            for frame in itertools.chain(container.decode(audio=0), [None]):
                if time.monotonic() - started > 60:
                    raise HTTPException(400, '音频解码耗时过长，请换成较短的普通录音')
                if frame is not None:
                    frame.pts = None
                for output in resampler.resample(frame):
                    array = output.to_ndarray().reshape(-1)
                    count += array.size
                    if count > MAX_SECONDS * SAMPLE_RATE:
                        raise HTTPException(413, '录音不能超过 10 分钟，请分段处理')
                    buffer.write(array.astype(np.int16, copy=False).tobytes())
        if not count:
            raise HTTPException(400, '录音没有可识别的音频内容')
        audio = np.frombuffer(buffer.getvalue(), dtype=np.int16).astype(np.float32) / 32768.0
        return audio, count / SAMPLE_RATE
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, '无法解码此录音，请使用有效的普通音频文件') from exc


def _load_model(model):
    global _MODEL, _MODEL_NAME
    directory = _model_directory(model)
    # tokenizer.json is required: upstream otherwise attempts a network fallback.
    if not _ready(model):
        raise HTTPException(409, '本机语音模型未准备，请先点击准备模型')
    if _MODEL is None or _MODEL_NAME != str(directory):
        try:
            from faster_whisper import WhisperModel
            _MODEL = None
            _MODEL_NAME = None
            _MODEL = WhisperModel(str(directory), device='cpu', compute_type='int8',
                                 cpu_threads=4, num_workers=1, local_files_only=True)
            _MODEL_NAME = str(directory)
        except Exception as exc:
            raise HTTPException(503, '本机语音模型无法加载，请检查模型和语音依赖') from exc
    return _MODEL


def transcribe(body, filename, model='base', language='zh'):
    model = _model_name(model)
    if language not in ('zh', 'auto'):
        raise HTTPException(400, '请选择中文或自动检测语言')
    validate_upload(body, filename)
    _require_dependencies()
    if not _OPERATION.acquire(blocking=False):
        raise HTTPException(409, '已有语音准备或转写任务正在处理，请稍后重试')
    _set_state(phase='transcribing', model=model, error='')
    try:
        if not _ready(model):
            raise HTTPException(409, '本机语音模型未准备，请先点击准备模型')
        audio, duration = _decode_audio(body)
        if not math.isfinite(duration) or duration <= 0 or duration > MAX_SECONDS:
            raise HTTPException(413, '录音时长必须在 10 分钟以内')
        recognizer = _load_model(model)
        segments, info = recognizer.transcribe(audio, language=None if language == 'auto' else language,
                                              beam_size=3, vad_filter=True, condition_on_previous_text=False)
        results = []
        total_text = 0
        started = time.monotonic()
        for segment in segments:
            text = str(segment.text).strip()
            total_text += len(text)
            if len(results) >= 3000 or total_text > 120_000 or time.monotonic() - started > 1200:
                raise HTTPException(400, '转写内容或耗时超过处理范围，请使用更短的录音')
            start, end = float(segment.start), float(segment.end)
            if not all(math.isfinite(value) for value in (start, end)):
                raise HTTPException(400, '语音模型返回无效时间')
            results.append({'start': max(0, min(duration, start)), 'end': max(0, min(duration, end)), 'text': text})
        result = {'text': '\n'.join(row['text'] for row in results if row['text']),
                  'language': str(info.language), 'duration': round(duration, 3),
                  'segments': results, 'model': model, 'engine': 'faster-whisper-local',
                  'saved': False, 'warning': '转写可能误识别，请对照录音修改后再确认保存'}
        _set_state(phase='ready', model=model, error='')
        return result
    except HTTPException as exc:
        _set_state(phase='failed', model=model, error=exc.detail)
        raise
    except Exception as exc:
        _set_state(phase='failed', model=model, error='本机语音转写失败，请检查录音与本地模型')
        raise HTTPException(503, '本机语音转写失败，请检查录音与本地模型') from exc
    finally:
        _OPERATION.release()
