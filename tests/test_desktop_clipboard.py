"""Synthetic bitmaps only: never reads or changes the user's clipboard."""
import os
import gc
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image
import pytest

from scripts import desktop_clipboard as clipboard
from scripts import desktop_control as desktop
from scripts import quick_capture as capture


def bitmap():
    return Image.new('RGB', (40, 20), '#ae5533')


def test_explicit_bitmap_becomes_png_inside_data_and_only_owned_file_is_cleaned(tmp_path):
    data = tmp_path / 'data'
    store = clipboard.TemporaryScreenshots(data)
    assert not data.exists()  # Importing or constructing never reads clipboard or writes.
    path = store.capture_clipboard(grabber=bitmap)
    assert path.parent.parent == data.resolve() / 'capture-temp'
    assert path.suffix == '.png'
    with Image.open(path) as image:
        assert image.size == (40, 20) and image.getpixel((0, 0)) == (174, 85, 51)
    foreign = store.root / '用户原有截图.png'
    foreign.write_bytes(b'never delete')
    assert store.cleanup([path, foreign]) == []
    assert not path.exists() and foreign.read_bytes() == b'never delete'
    assert store.root.exists()


@pytest.mark.parametrize('value', [None, ['复制的文件.png'], '剪贴板文字'])
def test_non_bitmap_is_rejected_without_creating_local_files(tmp_path, value):
    store = clipboard.TemporaryScreenshots(tmp_path / 'data')
    reader = Mock(return_value=value)
    with pytest.raises(desktop.ControlError, match='没有图片'):
        store.capture_clipboard(grabber=reader)
    reader.assert_called_once_with()
    assert not list(tmp_path.iterdir())


def test_clipboard_busy_and_oversized_bitmap_give_recoverable_message(tmp_path, monkeypatch):
    store = clipboard.TemporaryScreenshots(tmp_path / 'data')
    with pytest.raises(desktop.ControlError, match='重新复制'):
        store.capture_clipboard(grabber=Mock(side_effect=OSError('busy')))
    monkeypatch.setattr(clipboard, 'MAX_SCREENSHOT_PIXELS', 500)
    with pytest.raises(desktop.ControlError, match='尺寸过大'):
        store.capture_clipboard(grabber=bitmap)
    assert not list(tmp_path.iterdir())


def test_oversized_encoded_file_is_removed_without_retaining_invalid_attachment(tmp_path, monkeypatch):
    store = clipboard.TemporaryScreenshots(tmp_path / 'data')
    monkeypatch.setattr(clipboard, 'MAX_SCREENSHOT_BYTES', 4)
    with pytest.raises(desktop.ControlError, match='超过 100 MB'):
        store.capture_clipboard(grabber=bitmap)
    assert not store.owned and not list(store.root.rglob('*.png'))


def test_changed_or_unowned_files_are_preserved_on_cleanup(tmp_path):
    store = clipboard.TemporaryScreenshots(tmp_path / 'data')
    path = store.capture_clipboard(grabber=bitmap)
    other_window = clipboard.TemporaryScreenshots(tmp_path / 'data')
    foreign = other_window.capture_clipboard(grabber=bitmap)
    path.write_bytes(b'changed by external editor')
    assert store.cleanup([path, foreign]) == [path]
    assert path.read_bytes() == b'changed by external editor'
    assert foreign.exists()


def test_exit_without_submission_preserves_recoverable_image(tmp_path):
    store = clipboard.TemporaryScreenshots(tmp_path / 'data')
    path = store.capture_clipboard(grabber=bitmap)
    reopened = clipboard.TemporaryScreenshots(tmp_path / 'data')
    assert reopened.cleanup([path]) == []
    assert path.exists() and capture.inspect_file(path).size > 0


def test_redirected_temp_directory_is_rejected_and_external_files_preserved(tmp_path):
    data, outside = tmp_path / 'data', tmp_path / 'outside'
    data.mkdir()
    outside.mkdir()
    try:
        (data / 'capture-temp').symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip('Creating symlinks requires Windows developer mode or privileges')
    store = clipboard.TemporaryScreenshots(data)
    with pytest.raises(desktop.ControlError, match='重定向'):
        store.capture_clipboard(grabber=bitmap)
    assert not list(outside.iterdir())


def _until(root, predicate):
    deadline = time.monotonic() + 5
    while not predicate():
        root.update()
        if time.monotonic() > deadline:
            pytest.fail('Capture operation did not finish')
        time.sleep(.01)
    root.update()


def _exercise_capture_save_lifecycle(tmp_path, monkeypatch):
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    root.withdraw()
    config = desktop.load_config(tmp_path, {'APP_PORT': '8123'})
    manager = SimpleNamespace(config=config, start=Mock())
    ui = capture.QuickCaptureWindow(root, manager, desktop.SKINS['classic'])
    original_capture = ui.screenshots.capture_clipboard
    monkeypatch.setattr(ui.screenshots, 'capture_clipboard', lambda: original_capture(grabber=bitmap))
    try:
        ui.paste_button.invoke()
        _until(root, lambda: not ui.busy)
        assert len(ui.paths) == 1
        path = ui.paths[0]
        assert path.exists()
        ui.hide()
        assert path.exists() and ui.window.state() == 'withdrawn'
        refusal = Mock(return_value=False)
        monkeypatch.setattr(messagebox, 'askokcancel', refusal)
        assert ui.can_close_parent() is False
        assert str(ui.screenshots.recovery_directory) in refusal.call_args.args[1]

        def fail(draft, ignored_manager):
            draft.record_saved = True
            raise desktop.ControlError('模拟附件暂时未上传')
        monkeypatch.setattr(capture.CaptureDraft, 'submit', fail)
        ui.save()
        _until(root, lambda: not ui.busy)
        assert path.exists() and ui.draft.record_saved
        assert str(ui.paste_button.cget('state')) == 'disabled'

        def success(draft, ignored_manager):
            draft.uploaded.add(str(path.resolve()))
            return {'uuid': draft.uuid, 'attachments': 1}
        monkeypatch.setattr(capture.CaptureDraft, 'submit', success)
        ui.save()
        _until(root, lambda: not ui.busy)
        assert not path.exists() and ui.paths == [] and ui.draft is None
        assert ui.can_close_parent() is True
        assert str(ui.paste_button.cget('state')) == 'normal'
        manager.start.assert_not_called()
    finally:
        ui.destroy()
        root.destroy()
        del ui, root
        gc.collect()


def _exercise_capture_limit_and_recovery(tmp_path, monkeypatch):
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    manager = SimpleNamespace(config=desktop.load_config(tmp_path, {'APP_PORT': '8123'}))
    ui = capture.QuickCaptureWindow(root, manager, desktop.SKINS['classic'])
    reader = Mock()
    try:
        monkeypatch.setattr(ui.screenshots, 'capture_clipboard', reader)
        ui.paths = [tmp_path / str(index) for index in range(capture.MAX_FILES)]
        ui.paste_screenshot()
        reader.assert_not_called()
        assert not ui.busy and '最多 20' in ui.status.get()
        path = clipboard.TemporaryScreenshots.capture_clipboard(ui.screenshots, grabber=bitmap)
        ui.paths = [path]
        ui.draft = capture.CaptureDraft('原文已保存，附件稍后补充。', (path,), record_saved=True)
        ui.next_record()
        assert ui.paths == [] and ui.draft is None and path.exists()
        assert str(ui.screenshots.recovery_directory) in ui.status.get()
    finally:
        ui.destroy()
        root.destroy()
        del ui, root
        gc.collect()


def _run_ui_exercise(name, tmp_path):
    # Each Tk lifecycle owns one interpreter in a fresh process, matching the
    # existing --check-ui tests. Whole-suite extension imports and Tcl interpreter
    # teardown must not change whether the next capture test can initialize Tk.
    source = (
        "import runpy,sys; from pathlib import Path; import pytest; "
        "checks=runpy.run_path(sys.argv[1]); "
        "patch=pytest.MonkeyPatch(); "
        "checks[sys.argv[2]](Path(sys.argv[3]),patch); patch.undo()"
    )
    result = subprocess.run([sys.executable, '-c', source, str(Path(__file__).resolve()),
                             name, str(tmp_path)], cwd=desktop.ROOT,
                            env=os.environ | {'PYTHONUTF8': '1'}, capture_output=True,
                            text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stderr
    assert not result.stderr


@pytest.mark.skipif(os.name != 'nt' and not os.environ.get('DISPLAY'), reason='Tk display unavailable')
def test_capture_ui_retains_failed_image_and_unsaved_confirmation_then_cleans_verified_save(tmp_path):
    _run_ui_exercise('_exercise_capture_save_lifecycle', tmp_path)


@pytest.mark.skipif(os.name != 'nt' and not os.environ.get('DISPLAY'), reason='Tk display unavailable')
def test_capture_limit_never_reads_clipboard_and_move_next_retains_unuploaded_image(tmp_path):
    _run_ui_exercise('_exercise_capture_limit_and_recovery', tmp_path)
