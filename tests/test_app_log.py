import logging
import sys
import threading

import pytest

import app_log


@pytest.fixture
def fresh_log(tmp_path, monkeypatch):
    monkeypatch.setattr(app_log, "_handler", None)
    original_main, original_thread = sys.excepthook, threading.excepthook
    path = app_log.setup("9.9.9", tmp_path / "pixthrough.log")
    yield path
    app_log.logger.removeHandler(app_log._handler)
    app_log._handler.close()
    sys.excepthook, threading.excepthook = original_main, original_thread


def _text(path):
    for handler in app_log.logger.handlers:
        handler.flush()
    return path.read_text(encoding="utf-8")


def test_setup_writes_start_line_and_module_messages(fresh_log):
    app_log.get("bestcut").info("미리보기 끝: %d장", 12)

    text = _text(fresh_log)
    assert "시작: PixThrough 9.9.9" in text and "CPU" in text
    assert "pixthrough.bestcut: 미리보기 끝: 12장" in text


def test_setup_twice_does_not_duplicate_lines(fresh_log):
    app_log.setup("9.9.9", fresh_log)
    app_log.get("x").info("한 번만")

    assert _text(fresh_log).count("한 번만") == 1


def test_uncaught_errors_are_logged_with_traceback(fresh_log):
    shown = []
    app_log.install_excepthooks(on_error=shown.append)
    try:
        raise ValueError("깨진 사진")
    except ValueError:
        sys.excepthook(*sys.exc_info())

    thread = threading.Thread(target=lambda: 1 / 0, name="worker-1")
    thread.start()
    thread.join()

    text = _text(fresh_log)
    assert "처리하지 못한 오류" in text and "ValueError: 깨진 사진" in text and "Traceback" in text
    assert "작업 스레드에서 처리하지 못한 오류 (worker-1)" in text and "ZeroDivisionError" in text
    assert shown == ["ValueError: 깨진 사진"]


def test_log_rotates_when_large(tmp_path, monkeypatch):
    monkeypatch.setattr(app_log, "_handler", None)
    monkeypatch.setattr(app_log, "MAX_BYTES", 2000)
    app_log.setup("1.0", tmp_path / "pixthrough.log")
    try:
        for i in range(200):
            app_log.get("t").info("줄 %d %s", i, "x" * 40)
        assert (tmp_path / "pixthrough.log.1").exists()
        assert not (tmp_path / "pixthrough.log.5").exists()
    finally:
        app_log.logger.removeHandler(app_log._handler)
        app_log._handler.close()
    assert logging.getLogger("pixthrough").handlers == []
