"""Explicit bitmap paste into owned local files; no clipboard polling or network."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
import threading
import uuid

from scripts.desktop_control import ControlError

MAX_SCREENSHOT_PIXELS = 20_000_000
MAX_SCREENSHOT_BYTES = 100 * 1024**2


@dataclass(frozen=True)
class _OwnedFile:
    path: Path
    identity: tuple[int, int, int, int]


def _identity(path):
    record = path.stat(follow_symlinks=False)
    return record.st_dev, record.st_ino, record.st_size, record.st_mtime_ns


class TemporaryScreenshots:
    """Remove only files created by this capture window and still unchanged.

    Unsubmitted images survive withdrawal, failures, and process exit. They can be
    selected again from the displayed folder, without a background cleanup sweep.
    """
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir).resolve()
        self.root = self.data_dir / 'capture-temp'
        self.session = self.root / uuid.uuid4().hex
        self.owned = {}
        self.lock = threading.Lock()

    @property
    def recovery_directory(self):
        return self.root

    def _directory(self):
        # Reject symlinks and Windows junctions at both controlled boundaries.
        for path in (self.root, self.session):
            if path.is_symlink() or path.resolve() != path:
                raise ControlError('截图暂存目录已被重定向，请检查本机数据目录。')
            path.mkdir(parents=True, exist_ok=True)
            if path.is_symlink() or path.resolve() != path or not path.is_dir():
                raise ControlError('截图暂存目录不可用，请检查本机数据目录。')
        return self.session

    def capture_clipboard(self, *, grabber=None):
        """Called once per user click; copied file lists are never imported."""
        from PIL import Image, ImageGrab
        if grabber is None:
            if os.name != 'nt':
                raise ControlError('当前系统请使用“选择截图 / 文件”添加截图。')
            grabber = ImageGrab.grabclipboard
        try:
            bitmap = grabber()
        except (OSError, RuntimeError):
            raise ControlError('剪贴板暂时无法读取。请重新复制截图后再点“粘贴截图”。') from None
        if not isinstance(bitmap, Image.Image):
            raise ControlError('剪贴板里没有图片。请先复制截图，或使用“选择截图 / 文件”。')
        path = None
        created = False
        try:
            width, height = bitmap.size
            if width <= 0 or height <= 0 or width * height > MAX_SCREENSHOT_PIXELS:
                raise ControlError('截图尺寸过大，请裁剪后重新复制，或直接选择图片文件。')
            directory = self._directory()
            name = '截图_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:8] + '.png'
            path = directory / name
            # A fresh PNG retains pixel evidence, without importing arbitrary metadata.
            image = bitmap.convert('RGBA' if 'A' in bitmap.getbands() else 'RGB')
            try:
                with path.open('xb') as target:
                    created = True
                    image.save(target, format='PNG')
                if path.stat().st_size > MAX_SCREENSHOT_BYTES:
                    raise ControlError('截图文件超过 100 MB，请裁剪后重新复制。')
                record = _OwnedFile(path, _identity(path))
                with self.lock:
                    self.owned[path] = record
            finally:
                image.close()
        except (OSError, ValueError) as exc:
            if created:
                path.unlink(missing_ok=True)
            if isinstance(exc, ControlError):
                raise
            raise ControlError('截图未能暂存到数据目录，请检查可用空间与权限。') from None
        except ControlError:
            if created:
                path.unlink(missing_ok=True)
            raise
        finally:
            bitmap.close()
        return path

    def cleanup(self, paths):
        """Explicit removal or verified successful upload only; never recurse."""
        leftovers = []
        with self.lock:
            for raw in paths:
                path = Path(raw)
                record = self.owned.get(path)
                if record is None:
                    continue
                try:
                    if (self.root.is_symlink() or self.session.is_symlink()
                            or self.session.resolve() != self.session or path.is_symlink()
                            or path.resolve().parent != self.session
                            or _identity(path) != record.identity):
                        leftovers.append(path)
                        continue
                    path.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    leftovers.append(path)
                    continue
                self.owned.pop(path, None)
            if not self.owned:
                # Empty owned directories only. Existing files or another window's
                # directory are always retained.
                for folder in (self.session, self.root):
                    try:
                        if not folder.is_symlink() and folder.resolve() == folder:
                            folder.rmdir()
                    except OSError:
                        pass
        return leftovers
