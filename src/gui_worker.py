"""미리보기(스캔·스코어링)처럼 오래 걸리는 작업을 중간에 멈출 수 있는 QThread 바탕 클래스.

수천 장 미리보기는 몇 분이 걸리는데, 폴더를 잘못 골랐을 때 끝까지 기다려야 했다.
작업 쪽 코드(scanner/scoring 등)는 Qt를 모르므로 손대지 않고, 이미 사진 1장마다
불리는 진행률 콜백에서 취소 요청을 확인해 예외로 빠져나온다 — 그래서 늦어도 사진
한 장 처리 시간 안에 멈춘다. 그때까지 계산한 점수·지문은 캐시에 이미 들어가 있으니
버려지지 않는다(다시 미리보기하면 이어서 계산).
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QThread, Signal

import app_log

log = app_log.get("worker")


class PreviewCancelled(Exception):
    """사용자가 취소를 눌러 작업을 멈출 때 진행률 콜백에서 던진다."""


class CancellableWorker(QThread):
    cancelled = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._cancel_requested = False

    def request_cancel(self) -> None:
        """GUI 스레드에서 부른다. 작업 스레드가 다음 확인 지점에서 멈춘다."""
        self._cancel_requested = True

    def check_cancel(self) -> None:
        """작업 스레드의 진행률 콜백·반복문 안에서 부른다."""
        if self._cancel_requested:
            raise PreviewCancelled


class FunctionWorker(CancellableWorker):
    """fn(on_progress)를 작업 스레드에서 돌려 결과를 finished_ok로 넘긴다. 진행률 콜백이
    불릴 때마다 취소를 확인한다(진행률을 알리지 않는 작업은 끝까지 돈다)."""

    progress = Signal(int, int)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[[Callable[[int, int], None]], object], name: str = "작업") -> None:
        super().__init__()
        self._fn = fn
        self._name = name

    def run(self) -> None:
        def on_progress(done: int, total: int) -> None:
            self.progress.emit(done, total)
            self.check_cancel()

        try:
            self.finished_ok.emit(self._fn(on_progress))
        except PreviewCancelled:
            self.cancelled.emit()
        except Exception as e:  # noqa: BLE001 - surfaced to the user, not swallowed
            log.exception("%s 실패", self._name)
            self.failed.emit(str(e))
