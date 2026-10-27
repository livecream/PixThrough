"""앱 기록(로그): 무엇을 했고 어디서 오류가 났는지 파일로 남긴다.

화면 메시지만으로는 사용자 컴퓨터에서 생긴 문제(업데이트 실패, 특정 사진에서 멈춤 등)를
원격으로 알기 어려워, 앱 데이터 폴더(update.log 옆)에 pixthrough.log를 쓴다.
- 크기가 1MB를 넘으면 pixthrough.log.1~3으로 돌려 쓴다(오래된 것부터 버림).
- 시작할 때 버전·OS·실행 방식(앱/소스)·CPU 수를 한 줄 남긴다.
- 작업(미리보기·복사/이동·되돌리기·별점·축소·정렬·정리)은 요약 한 줄, 오류는 전체 추적까지.
- 화면에서 잡지 못한 오류(메인 스레드·작업 스레드)도 기록한다(install_excepthooks).
- 기록은 이 컴퓨터에만 남는다. 문제를 알릴 때 사용자가 직접 보내면 된다.
메뉴 "도움말 > 로그 폴더 열기"가 이 폴더를 연다.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import platform
import sys
import threading
from pathlib import Path
from typing import Callable

LOG_NAME = "pixthrough.log"
MAX_BYTES = 1_000_000
BACKUP_COUNT = 3

logger = logging.getLogger("pixthrough")
_handler: logging.Handler | None = None


def app_data_dir() -> Path:
    """모델·점수 캐시·update.log가 있는 앱 데이터 폴더(소스 실행이면 저장소 폴더)."""
    import score_store  # 무거운 모듈(mediapipe 등)을 기록 설정 전에 불러오지 않게 여기서

    return score_store.cache_dir().parent


def log_path() -> Path:
    return app_data_dir() / LOG_NAME


def _os_description() -> str:
    if sys.platform == "darwin":
        return f"macOS {platform.mac_ver()[0]} ({platform.machine()})"
    if sys.platform == "win32":
        return f"Windows {platform.version()} ({platform.machine()})"
    return f"{platform.system()} {platform.release()} ({platform.machine()})"


def setup(version: str, path: Path | None = None) -> Path:
    """파일 기록을 켠다(여러 번 불러도 한 번만). 기록 파일 경로를 돌려준다."""
    global _handler
    path = path or log_path()
    if _handler is None:
        path.parent.mkdir(parents=True, exist_ok=True)
        _handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
        )
        _handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
        )
        logger.addHandler(_handler)
        logger.setLevel(logging.INFO)
    logger.info(
        "시작: PixThrough %s · %s · Python %s · %s 실행 · CPU %s개",
        version,
        _os_description(),
        platform.python_version(),
        "앱" if getattr(sys, "frozen", False) else "소스",
        os.cpu_count(),
    )
    return path


def install_excepthooks(on_error: Callable[[str], None] | None = None) -> None:
    """잡지 못한 오류를 기록한다. on_error가 있으면 메인 스레드 오류를 사용자에게도 알린다
    (짧은 메시지 한 줄)."""

    def main_hook(exc_type, exc, tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        logger.error("처리하지 못한 오류", exc_info=(exc_type, exc, tb))
        if on_error is not None:
            try:
                on_error(f"{exc_type.__name__}: {exc}")
            except Exception:  # noqa: BLE001 - 알림이 실패해도 기록은 이미 남음
                logger.exception("오류 알림을 띄우지 못함")

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        logger.error(
            "작업 스레드에서 처리하지 못한 오류 (%s)",
            args.thread.name if args.thread else "?",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = main_hook
    threading.excepthook = thread_hook


def get(name: str) -> logging.Logger:
    """모듈별 기록기(pixthrough.<name>). setup 전에는 아무 데도 쓰지 않는다."""
    return logging.getLogger(f"pixthrough.{name}")
