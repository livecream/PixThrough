"""표 옆에 고른 파일의 사진을 작게 보여주는 칸. 시간순 정렬·폴더 정리처럼 썸네일을 미리
만들지 않는 화면에서, 고른 줄의 사진만 작업 스레드에서 읽어 와 화면이 멈추지 않게 한다.
영상은 한 장면(video_frame)과 길이·크기를 보여준다."""

from __future__ import annotations

import io
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFrame, QLabel, QSizePolicy, QVBoxLayout

from rename_by_time import VIDEO_EXTENSIONS
from thumbnails import make_thumbnail_jpeg
from video_frame import format_duration, video_frame

PREVIEW_SIZE = 240
_CACHE_SIZE = 64


class _Signals(QObject):
    loaded = Signal(object, object, str)  # 경로, JPEG 바이트(실패면 None), 덧붙일 설명(영상 길이 등)


def _load_video(path: Path) -> tuple[bytes | None, str]:
    result = video_frame(path, (PREVIEW_SIZE, PREVIEW_SIZE))
    if result is None:
        return None, "🎬 영상 (미리보기를 읽지 못함)"
    image, info = result
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=85)
    parts = ["🎬 영상", format_duration(info.duration), f"{info.width}×{info.height}" if info.width else ""]
    return buffer.getvalue(), " · ".join(p for p in parts if p)


class _LoadTask(QRunnable):
    def __init__(self, path: Path, signals: _Signals) -> None:
        super().__init__()
        self._path = path
        self._signals = signals

    def run(self) -> None:
        if self._path.suffix.lower() in VIDEO_EXTENSIONS:
            data, note = _load_video(self._path)
        else:
            data, note = make_thumbnail_jpeg(self._path, (PREVIEW_SIZE, PREVIEW_SIZE)), ""
        self._signals.loaded.emit(self._path, data, note)


class PreviewPane(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFixedWidth(PREVIEW_SIZE + 24)
        self._image = QLabel()
        self._image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image.setMinimumHeight(PREVIEW_SIZE)
        self._caption = QLabel()
        self._caption.setWordWrap(True)
        self._caption.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self._caption.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._caption.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout()
        layout.addWidget(self._image)
        layout.addWidget(self._caption)
        layout.addStretch(1)
        self.setLayout(layout)
        self._path: Path | None = None
        self._caption_text = ""
        self._cache: OrderedDict[Path, tuple[bytes | None, str]] = OrderedDict()
        self._signals = _Signals()
        self._signals.loaded.connect(self._on_loaded)
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(1)
        self.set_path(None)

    def set_path(self, path: Path | None, caption: str = "") -> None:
        self._path = path
        self._caption_text = caption or (path.name if path else "")
        self._caption.setText(self._caption_text)
        self._image.setPixmap(QPixmap())
        if path is None:
            self._image.setText("줄을 고르면\n사진이 여기 보입니다")
            return
        if path in self._cache:
            self._show(*self._cache[path])
            return
        self._image.setText("불러오는 중…")
        self._pool.clear()  # 아직 시작 안 한 예전 요청은 버린다(빠르게 넘길 때)
        self._pool.start(_LoadTask(path, self._signals))

    def _on_loaded(self, path: Path, data: bytes | None, note: str) -> None:
        self._cache[path] = (data, note)
        while len(self._cache) > _CACHE_SIZE:
            self._cache.popitem(last=False)
        if path == self._path:
            self._show(data, note)

    def _show(self, data: bytes | None, note: str = "") -> None:
        if note:
            self._caption.setText(f"{self._caption_text}\n{note}")
        pixmap = QPixmap()
        if data is None or not pixmap.loadFromData(data, "JPEG"):
            self._image.setText("🎬" if note else "사진을 열 수 없습니다")
            return
        self._image.setText("")
        self._image.setPixmap(pixmap)

    def wait(self) -> None:
        """테스트용: 읽고 있는 사진이 끝날 때까지 기다린다."""
        self._pool.waitForDone()
