"""Explicit one-off official GGUF preparation; never reads or sends user records."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import HOME
from app.services.local_ai import ensure_runtime, stop_runtime

REPOSITORY = 'Qwen/Qwen2.5-1.5B-Instruct-GGUF'
REVISION = '91cad51170dc346986eccefdc2dd33a9da36ead9'
FILENAME = 'qwen2.5-1.5b-instruct-q4_k_m.gguf'
SHA256 = '6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e'
SIZE = 1117320736
MODEL = 'qwen2.5:1.5b'
URL = f'https://huggingface.co/{REPOSITORY}/resolve/{REVISION}/{FILENAME}'


class OfficialRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        from urllib.parse import urlsplit
        value = urlsplit(newurl)
        host = value.hostname or ''
        if value.scheme != 'https' or not (host == 'huggingface.co' or host.endswith('.huggingface.co') or host.endswith('.hf.co')):
            raise ValueError('Official model download redirected outside the approved HTTPS distribution domains')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def main():
    directory = HOME / 'models' / 'gguf'
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / FILENAME
    partial = target.with_suffix('.gguf.part')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), OfficialRedirects(),
                                        urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    if not target.exists():
        for attempt in range(4):
            offset = partial.stat().st_size if partial.exists() else 0
            headers = {'User-Agent': 'PersonalKnowledgeDatabase-model-prepare/1'}
            if offset:
                headers['Range'] = f'bytes={offset}-'
            print(f'download attempt={attempt + 1} bytes={offset}/{SIZE}', flush=True)
            try:
                with opener.open(urllib.request.Request(URL, headers=headers), timeout=90) as response:
                    if offset and (response.status != 206 or not response.headers.get('Content-Range', '').startswith(f'bytes {offset}-')):
                        raise ValueError('Resume range was not acknowledged; refusing to mix model contents')
                    with partial.open('ab' if offset else 'wb') as stream:
                        last = time.monotonic()
                        completed = offset
                        while block := response.read(1024 * 1024):
                            completed += len(block)
                            if completed > SIZE:
                                raise ValueError('Official model response exceeded expected size')
                            stream.write(block)
                            if time.monotonic() - last >= 3:
                                print(f'download {completed / SIZE:.1%} bytes={completed}/{SIZE}', flush=True)
                                last = time.monotonic()
                if partial.stat().st_size != SIZE:
                    raise ValueError('Download ended before expected model size')
                partial.replace(target)
                break
            except Exception as exc:
                print(f'download retry: {type(exc).__name__}: {exc}', flush=True)
                if attempt == 3:
                    raise
                time.sleep(3)
    with target.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if target.stat().st_size != SIZE or digest != SHA256:
        raise ValueError('Official model SHA256 or size mismatch; refusing import')
    print(f'SHA256 verified: {digest}', flush=True)
    modelfile = directory / 'Qwen2.5-1.5B.Modelfile'
    # ChatML roles match the Qwen2.5 Instruct training template.
    modelfile.write_text('FROM "' + target.as_posix() + '"\n'
                        'TEMPLATE """{{- if .System }}<|im_start|>system\n{{ .System }}<|im_end|>\n{{ end }}'
                        '{{- range .Messages }}{{ if ne .Role "system" }}<|im_start|>{{ .Role }}\n{{ .Content }}<|im_end|>\n{{ end }}{{ end }}<|im_start|>assistant\n"""\n'
                        'PARAMETER stop "<|im_start|>"\nPARAMETER stop "<|im_end|>"\n', encoding='utf-8')
    config = {'provider': 'ollama', 'base_url': 'http://127.0.0.1:11435'}
    try:
        ensure_runtime(config)
        environment = dict(os.environ, OLLAMA_HOST=config['base_url'], OLLAMA_MODELS=str(HOME / 'models' / 'ollama'))
        subprocess.run([shutil.which('ollama'), 'create', MODEL, '-f', str(modelfile)], env=environment,
                       check=True, timeout=300, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        manifest = {'repository': REPOSITORY, 'revision': REVISION, 'filename': FILENAME, 'source_url': URL,
                    'sha256': digest, 'size': SIZE, 'model': MODEL, 'template': 'Qwen2.5 Instruct ChatML',
                    'prepared_at': time.strftime('%Y-%m-%dT%H:%M:%S%z')}
        (directory / 'model-source.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        print('Model import completed', flush=True)
    finally:
        stop_runtime()


if __name__ == '__main__':
    main()
