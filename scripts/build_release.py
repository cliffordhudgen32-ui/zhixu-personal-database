"""Build a reviewable, data-free source preview ZIP. Does not publish anything."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VERSION = '0.1.0-preview.5'

# Only these program directories, immediate source files and named resources are
# eligible. Runtime directories and the user's README are never traversed.
PROGRAM_DIRECTORIES = {
    'app': {'.py'},
    'app/services': {'.py'},
    'app/utils': {'.py'},
    'database/versions': {'.py'},
}
NAMED_FILES = (
    'alembic.ini', 'requirements.txt', 'requirements.lock.txt', 'requirements-speech.txt',
    'control.bat', 'run.bat', 'backup.bat',
    'database/env.py', 'database/script.py.mako',
    'scripts/launch.py', 'scripts/desktop_control.py', 'scripts/backup.py',
    'scripts/create_desktop_shortcut.ps1', 'scripts/prepare_official_model.py',
    'scripts/prepare_local_model.py', 'scripts/build_release.py', 'scripts/verify_release_runtime.py',
    'app/static/index.html', 'app/static/style.css', 'app/static/app.js',
    'app/static/lookups.js', 'app/static/ai-workflow.js', 'app/static/api-docs.js',
    'app/static/appearance.js', 'app/static/appearance.css', 'app/static/app.ico',
    'app/static/vendor/swagger-ui.css', 'app/static/vendor/swagger-ui-bundle.js',
    'app/static/vendor/LICENSE', 'docs/ROADMAP.md', 'docs/DISTRIBUTION.md', 'docs/schema.md',
)
OPTIONAL_FEATURE_FILES = (
    'scripts/quick_capture.py',
    'scripts/desktop_tray.py', 'requirements-speech.txt',
    'scripts/desktop_clipboard.py', 'scripts/desktop_hotkey.py',
    'app/static/ai-review-tools.js', 'app/static/ai-review-tools.css',
    'app/static/knowledge-answer.js', 'app/static/knowledge-answer.css',
    'app/static/period-review.js', 'app/static/period-review.css',
    'app/static/speech.js', 'app/static/speech.css',
    'app/static/capture-workspace.js', 'app/static/capture-workspace.css',
    'app/static/ai-followups.js', 'app/static/ai-followups.css',
)
USER_ART = ('maple-reading.jpg', 'maple-profile.jpg', 'maple-night.jpg', 'maple-portrait.jpg')
SAFE_APPEARANCE = {'skin': 'classic', 'illustrations': False, 'motion': False}
PLACEHOLDER = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 400" role="img" aria-label="枫叶图案">
<rect width="300" height="400" fill="#f7f2e9"/>
<circle cx="150" cy="187" r="93" fill="#efdfcb"/>
<path fill="#a95038" d="m150 99 18 50 28-24-3 44 38-3-29 35 17 18-53 11-11 39-6-39-53-11 17-18-28-35 38 3-3-44 27 24z"/>
<path d="m150 178 3 110" stroke="#805746" stroke-width="4" fill="none"/>
</svg>'''
PREVIEW_README = '''# 知序 · 本地个人知识数据库（试用候选包）

此 ZIP 是供审阅和少量试用的源代码包，尚未对外发布，不是独立 EXE 安装程序。

1. 将整个 ZIP 解压到有写入权限的空文件夹。需要 Python 3.11 或以上，Windows Python 需包含 Tkinter。
2. 双击 `control.bat` 打开桌面控制台，或 `run.bat` 打开网页。首次安装依赖需要联网。
3. 第一次进入欢迎设置。默认只监听本机，不支持多人或局域网访问。
4. 程序、数据库、附件、配置与备份默认位于解压目录。若需要分开存储，按 `.env.example` 配置新的数据目录；不要复制开发者的 `.env`。

本包不带任何个人数据库、附件、API 密钥、模型权重、日志、备份、私人截图和用户角色原图。
默认素白皮肤；暖色皮肤使用包内通用枫叶 SVG。OCR、向量模型、Ollama 与整理模型的准备方式及限制见 [试用与分发说明](docs/DISTRIBUTION.md)。

使用前先读 [试用与分发说明](docs/DISTRIBUTION.md)、[产品规划](docs/ROADMAP.md) 和 [第三方与素材清单](THIRD-PARTY-NOTICES.md)。
包内 `release-manifest.json` 记录文件清单、SHA256 和源码变体，可用于检查包内容。此候选包不代表已经完成发布许可检查。
'''
ENV_TEMPLATE = '''# Optional example only; this file is not active until renamed to .env.
APP_HOST=127.0.0.1
APP_PORT=8000
# Set a new absolute data directory if desired, e.g. D:/ZhixuData.
# PLD_HOME=D:/ZhixuData
# Leave DATABASE_URL, ATTACHMENT_DIR and BACKUP_DIR unset to follow PLD_HOME.
'''
NOTICES = '''# 第三方依赖与素材说明（发布前待完成）

- Python 依赖版本见 `requirements.lock.txt`。当前包不捆绑 Python、虚拟环境或依赖二进制。
- Swagger UI 静态资源带其原许可证：`app/static/vendor/LICENSE`。
- 本包不捆绑 Ollama 可执行程序或模型权重。正式分发前须逐项核对运行时、OCR/向量/整理模型的许可证、署名、版本和下载来源，并生成可复查的依赖清单。
- 用户提供的四张角色参考 JPEG 不在本包内。个人界面使用这些图片，不表示已经获得公开或商业分发授权。
- 包内 `maple-placeholder.svg` 是用于替换人物参考图的通用代码绘制枫叶图案。品牌名称、图标和商标检索，以及本项目自己的代码授权条款，仍需发布前确认。

这是许可梳理清单，不能替代完整许可证审查；在清单未完成前不将候选包标记为公开正式发行版。
'''


def _safe_source(root: Path, relative: str) -> Path:
    path = root / relative
    resolved = path.resolve()
    if path.is_symlink() or not resolved.is_relative_to(root) or not path.is_file():
        raise ValueError(f'源文件缺失或链接不安全：{relative}')
    return path


def _distribution_variant(relative: str, body: bytes) -> bytes:
    if relative not in ('app/static/appearance.js', 'app/static/appearance.css',
                        'app/services/appearance.py', 'scripts/desktop_control.py'):
        return body
    text = body.decode('utf-8')
    if relative == 'scripts/desktop_control.py':
        for image in USER_ART:
            text = text.replace(f"'art': '{image}'", "'art': None")
    else:
        for image in USER_ART:
            text = text.replace(image, 'maple-placeholder.svg')
    if relative == 'app/static/appearance.js':
        text = text.replace('skin: "maple-light", illustrations: true, motion: false',
                            'skin: "classic", illustrations: false, motion: false')
        for alt in ('银白发角色手持书本，衣袖饰有枫叶', '银白发角色站在秋日枫林与温暖灯火中'):
            text = text.replace(alt, '通用枫叶图案')
    if relative == 'app/services/appearance.py':
        text = text.replace("{'skin': 'maple-light', 'illustrations': True, 'motion': False}",
                            "{'skin': 'classic', 'illustrations': False, 'motion': False}")
    return text.encode('utf-8')


def collect_files(source_root: Path = ROOT) -> dict[str, bytes]:
    root = Path(source_root).resolve()
    files = {}
    for relative in NAMED_FILES:
        files[relative] = _distribution_variant(relative, _safe_source(root, relative).read_bytes())
    for relative in OPTIONAL_FEATURE_FILES:
        if (root / relative).exists():
            files[relative] = _safe_source(root, relative).read_bytes()
    for relative, suffixes in PROGRAM_DIRECTORIES.items():
        directory = root / relative
        if directory.is_symlink() or not directory.resolve().is_relative_to(root) or not directory.is_dir():
            raise ValueError(f'程序目录不安全：{relative}')
        for path in sorted(directory.iterdir()):
            if path.suffix in suffixes and not path.name.startswith('.'):
                name = path.relative_to(root).as_posix()
                files[name] = _distribution_variant(name, _safe_source(root, name).read_bytes())
    files.update({
        'README.md': PREVIEW_README.encode('utf-8'),
        '.env.example': ENV_TEMPLATE.encode('utf-8'),
        'appearance.json': (json.dumps(SAFE_APPEARANCE, ensure_ascii=False, indent=2) + '\n').encode('utf-8'),
        'THIRD-PARTY-NOTICES.md': NOTICES.encode('utf-8'),
        'app/static/skins/maple-placeholder.svg': PLACEHOLDER.encode('utf-8'),
    })
    return files


def build_release(output: Path, version: str = DEFAULT_VERSION, source_root: Path = ROOT) -> dict:
    if not isinstance(version, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', version):
        raise ValueError('版本只能使用字母、数字、点、下划线和连字符')
    output = Path(output).resolve()
    if output.suffix.lower() != '.zip' or output.exists():
        raise ValueError('输出必须为尚不存在的 ZIP 文件，不覆盖已有文件')
    files = collect_files(source_root)
    manifest = {
        'format': 'zhixu-source-preview', 'version': version,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'publication': 'not-published', 'license_review': 'pending',
        'default_appearance': SAFE_APPEARANCE,
        'source_variants': ['素白默认偏好', '用户角色引用替换为通用 SVG', '桌面人物插画关闭'],
        'excluded': ['personal data', 'active environment/configuration', 'API keys', 'models',
                     'logs', 'backups', 'exports', 'screenshots', 'user character JPEGs', 'virtual environments'],
        'sha256': {name: hashlib.sha256(body).hexdigest() for name, body in sorted(files.items())},
    }
    files['release-manifest.json'] = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    installed = False
    try:
        with tempfile.NamedTemporaryFile(prefix='.release-', suffix='.tmp', dir=output.parent, delete=False) as stream:
            temporary = Path(stream.name)
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, body in sorted(files.items()):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, body)
        # Exclusive creation also prevents a racing build from overwriting a ZIP.
        with output.open('xb') as dest, temporary.open('rb') as source:
            installed = True
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                dest.write(chunk)
            dest.flush()
            os.fsync(dest.fileno())
    except Exception:
        if installed:
            output.unlink(missing_ok=True)
        raise
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {'output': str(output), 'files': len(files), 'sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
            'version': version, 'publication': 'not-published'}


def main():
    parser = argparse.ArgumentParser(description='构建不含私人数据的少量试用候选 ZIP；不会发布')
    parser.add_argument('--version', default=DEFAULT_VERSION)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    output = args.output or ROOT / 'dist' / f'zhixu-{args.version}.zip'
    try:
        result = build_release(output, args.version)
    except (ValueError, OSError, UnicodeError, zipfile.BadZipFile) as exc:
        parser.exit(1, f'构建未完成：{exc}\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
