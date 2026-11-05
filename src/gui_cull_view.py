"""전체 화면 컬링: 사진 한 장을 화면 가득 띄우고 키보드만으로 빠르게 고른다(Photo Mechanic·
라이트룸 컬링처럼). P = 남김, X = 뺌(누르면 다음 사진으로 — "자동으로 다음 사진" 끄기 가능),
Space = 넣기/빼기 바꾸기(그대로), ←/→ = 앞뒤 사진, ↑/↓ = 앞뒤 장면, Z = 100% ↔ 맞춤,
C = 이 장면 나란히 비교, Esc = 닫기. 아래 줄에 지금 장면의 컷이 작게 보인다(클릭해서 이동).

베스트컷 화면과 같은 선택을 쓴다(넣기/빼기는 화면의 직접 고르기와 같음). 다음·이전 사진은
작업 스레드에서 미리 화면 크기로 읽어 두어 넘길 때 기다리지 않게 한다.
"""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import Callable

from PIL.ImageQt import ImageQt
from PySide6.QtCore import QObject, QRect, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QColor, QImage, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from report import ReportRow
from thumbnails import load_preview_image

KEPT = QColor(52, 168, 83)
FLAGGED = QColor(214, 54, 40)
CLIENT = QColor(26, 115, 232)
STRIP_THUMB = 72
_CACHE = 8


class _Signals(QObject):
    loaded = Signal(object, object)  # 경로, QImage(실패면 None)


class _Load(QRunnable):
    def __init__(self, path: Path, size: QSize, signals: _Signals) -> None:
        super().__init__()
        self._path, self._size, self._signals = path, size, signals

    def run(self) -> None:
        image = load_preview_image(self._path)
        qimage = None
        if image is not None:
            image.thumbnail((self._size.width(), self._size.height()))  # 화면보다 크게 들고 있지 않게
            qimage = QImage(ImageQt(image.convert("RGBA"))).copy()
        self._signals.loaded.emit(self._path, qimage)


class _Strip(QWidget):
    """지금 장면의 컷을 작게 늘어놓은 줄. 남김 초록·C컷 빨강 테두리, 고객 파란 점."""

    clicked = Signal(int)  # 장면 안 위치

    def __init__(self, view: CullDialog) -> None:
        super().__init__()
        self._view = view
        self.setFixedHeight(STRIP_THUMB + 16)

    def _rects(self) -> list[QRect]:
        count = len(self._view.scene_paths())
        step = STRIP_THUMB + 8
        left = max(8, (self.width() - count * step) // 2)
        return [QRect(left + i * step, 8, STRIP_THUMB, STRIP_THUMB) for i in range(count)]

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override signature
        painter = QPainter(self)
        current = self._view.current_path()
        for path, rect in zip(self._view.scene_paths(), self._rects()):
            row = self._view.row_for(path)
            pixmap = self._view.thumbnail(path)
            painter.setOpacity(1.0 if row.selected else 0.5)
            if pixmap is not None:
                scaled = pixmap.scaled(rect.size(), Qt.AspectRatioMode.KeepAspectRatio)
                painter.drawPixmap(rect.x() + (rect.width() - scaled.width()) // 2,
                                   rect.y() + (rect.height() - scaled.height()) // 2, scaled)
            else:
                painter.fillRect(rect, QColor(60, 60, 60))
            painter.setOpacity(1.0)
            border = KEPT if row.selected else FLAGGED if row.badcut_reason else None
            if border is not None:
                painter.setPen(QPen(border, 3))
                painter.drawRect(rect.adjusted(1, 1, -1, -1))
            if self._view.is_client(path):
                painter.setBrush(CLIENT)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(rect.right() - 14, rect.y() + 4, 10, 10)
                painter.setBrush(Qt.BrushStyle.NoBrush)
            if path == current:
                painter.setPen(QPen(QColor("white"), 2))
                painter.drawRect(rect.adjusted(-4, -4, 4, 4))

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override signature
        for i, rect in enumerate(self._rects()):
            if rect.contains(event.position().toPoint()):
                self.clicked.emit(i)
                return


class CullDialog(QDialog):
    def __init__(
        self,
        paths: list[Path],
        index: int,
        row_for: Callable[[Path], ReportRow],
        on_choose: Callable[[ReportRow, bool], None],
        on_toggle: Callable[[ReportRow], None],
        thumbnail: Callable[[Path], QPixmap | None],
        is_client: Callable[[Path], bool] = lambda path: False,
        on_compare: Callable[[Path], None] | None = None,
        auto_advance: bool = True,
        on_rate: Callable[[ReportRow, int], None] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("컬링 — 전체 화면으로 고르기")
        self.setStyleSheet("QDialog { background: #111; } QLabel, QCheckBox { color: #ddd; }")
        self._paths = paths
        self._index = index
        self.row_for = row_for
        self._on_choose = on_choose
        self._on_toggle = on_toggle
        self.thumbnail = thumbnail
        self.is_client = is_client
        self._on_compare = on_compare
        self._on_rate = on_rate
        self._actual = False
        self._cache: OrderedDict[Path, QImage | None] = OrderedDict()
        self._pending: set[Path] = set()
        self._signals = _Signals()
        self._signals.loaded.connect(self._on_loaded)
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(2)

        self._title = QLabel()
        self._auto_checkbox = QCheckBox("P/X·별점 누르면 다음 사진으로")
        self._auto_checkbox.setChecked(auto_advance)
        self._auto_checkbox.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        top = QHBoxLayout()
        top.addWidget(self._title, stretch=1)
        top.addWidget(self._auto_checkbox)
        self._image = QLabel()
        self._image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scroll = QScrollArea()
        self._scroll.setWidget(self._image)
        self._scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scroll.setStyleSheet("QScrollArea { border: none; background: #111; }")
        self._scroll.viewport().setStyleSheet("background: #111;")
        self._image.setStyleSheet("background: #111; color: #aaa;")
        self._scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._strip = _Strip(self)
        self._strip.clicked.connect(lambda i: self._go(self._scene_start() + i))
        help_label = QLabel(
            "P 남김 · X 뺌 · 1~5 별점(남김)·0 지우기 · Space 넣기/빼기 · ←/→ 사진 · ↑/↓ 장면 · Z 100%/맞춤 · "
            "C 나란히 비교 · Esc 닫기"
        )
        help_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        help_label.setStyleSheet("color: #888;")
        layout = QVBoxLayout()
        layout.addLayout(top)
        layout.addWidget(self._scroll, stretch=1)
        layout.addWidget(self._strip)
        layout.addWidget(help_label)
        self.setLayout(layout)

        for key, action in (
            ("Left", lambda: self._go(self._index - 1)),
            ("Right", lambda: self._go(self._index + 1)),
            ("Up", lambda: self._go_scene(-1)),
            ("Down", lambda: self._go_scene(1)),
            ("P", lambda: self._choose(True)),
            ("X", lambda: self._choose(False)),
            ("Space", self._toggle),
            ("Z", self._toggle_actual),
            ("C", self._compare),
        ):
            QShortcut(QKeySequence(key), self, activated=action)
        for stars in range(6):
            QShortcut(QKeySequence(str(stars)), self, activated=lambda s=stars: self._rate(s))
        self._show_current()

    # --- 데이터 ---------------------------------------------------------------------
    def auto_advance(self) -> bool:
        return self._auto_checkbox.isChecked()

    def current_path(self) -> Path:
        return self._paths[self._index]

    def _scene_of(self, index: int) -> int:
        return self.row_for(self._paths[index]).scene_id

    def _scene_start(self) -> int:
        scene = self._scene_of(self._index)
        start = self._index
        while start > 0 and self._scene_of(start - 1) == scene:
            start -= 1
        return start

    def scene_paths(self) -> list[Path]:
        scene = self._scene_of(self._index)
        start = self._scene_start()
        end = start
        while end < len(self._paths) and self._scene_of(end) == scene:
            end += 1
        return self._paths[start:end]

    def refresh(self) -> None:
        """선택이 바뀌었으면(이 창이나 베스트컷 화면에서) 제목·아래 줄을 다시 그린다."""
        self._update_title()
        self._strip.update()

    # --- 이동·고르기 ------------------------------------------------------------------
    def _go(self, index: int) -> None:
        if 0 <= index < len(self._paths) and index != self._index:
            self._index = index
            self._show_current()

    def _go_scene(self, delta: int) -> None:
        """앞·뒤 장면의 첫 컷으로."""
        start = self._scene_start()
        if delta > 0:
            self._go(start + len(self.scene_paths()))
            return
        if start == 0:
            return
        scene = self._scene_of(start - 1)
        target = start - 1
        while target > 0 and self._scene_of(target - 1) == scene:
            target -= 1
        self._go(target)

    def _choose(self, wanted: bool) -> None:
        self._on_choose(self.row_for(self.current_path()), wanted)
        self.refresh()
        if self.auto_advance():
            self._go(self._index + 1)

    def _toggle(self) -> None:
        self._on_toggle(self.row_for(self.current_path()))
        self.refresh()

    def _rate(self, stars: int) -> None:
        if self._on_rate is None:
            return
        self._on_rate(self.row_for(self.current_path()), stars)
        self.refresh()
        if stars and self.auto_advance():
            self._go(self._index + 1)

    def _compare(self) -> None:
        if self._on_compare is not None:
            self._on_compare(self.current_path())

    def _toggle_actual(self) -> None:
        self._actual = not self._actual
        self._render()

    # --- 그리기 -----------------------------------------------------------------------
    def _update_title(self) -> None:
        path = self.current_path()
        row = self.row_for(path)
        scene_paths = self.scene_paths()
        state = "✓ 남김" if row.selected else "C컷" if row.badcut_reason else "빠짐"
        if row.manual_choice is not None:
            state += " (직접)"
        if row.rating:
            state += " " + "★" * row.rating
        if self.is_client(path):
            state += " · 고객 선택"
        scenes = len({self.row_for(p).scene_id for p in self._paths})
        self._title.setText(
            f"<b>{path.name}</b>   ·   {state}   ·   장면 {row.scene_id} ({scene_paths.index(path) + 1}/"
            f"{len(scene_paths)})   ·   전체 {self._index + 1}/{len(self._paths)}   ·   장면 {scenes}개"
        )
        color = KEPT if row.selected else FLAGGED if row.badcut_reason else QColor("#ddd")
        self._title.setStyleSheet(f"color: {color.name()};")

    def _show_current(self) -> None:
        self._update_title()
        self._strip.update()
        self._render()
        for offset in (1, 2, -1):  # 넘길 사진을 미리
            neighbor = self._index + offset
            if 0 <= neighbor < len(self._paths):
                self._request(self._paths[neighbor])

    def _target_size(self) -> QSize:
        screen = self.screen().availableSize() if self.screen() else QSize(1920, 1080)
        return QSize(max(screen.width(), 800), max(screen.height(), 600))

    def _request(self, path: Path) -> None:
        if path in self._cache or path in self._pending:
            return
        self._pending.add(path)
        self._pool.start(_Load(path, self._target_size(), self._signals))

    def _on_loaded(self, path: Path, image: QImage | None) -> None:
        self._pending.discard(path)
        self._cache[path] = image
        while len(self._cache) > _CACHE:
            self._cache.popitem(last=False)
        if path == self.current_path() and not self._actual:
            self._render()

    def _render(self) -> None:
        path = self.current_path()
        if self._actual:
            full = load_preview_image(path)  # 100%는 원본 그대로(가끔만 쓰니 그때 읽음)
            if full is None:
                self._image.setText("이미지를 열 수 없습니다")
                return
            self._scroll.setWidgetResizable(False)
            self._image.setPixmap(QPixmap.fromImage(ImageQt(full.convert("RGBA"))))
            self._image.adjustSize()
            bar_h, bar_v = self._scroll.horizontalScrollBar(), self._scroll.verticalScrollBar()
            bar_h.setValue(bar_h.maximum() // 2)
            bar_v.setValue(bar_v.maximum() // 2)
            return
        self._scroll.setWidgetResizable(True)
        if path not in self._cache:
            self._image.setPixmap(QPixmap())
            self._image.setText("불러오는 중…")
            self._request(path)
            return
        image = self._cache[path]
        if image is None:
            self._image.setPixmap(QPixmap())
            self._image.setText("이미지를 열 수 없습니다 (미리보기가 없는 RAW 또는 손상된 파일)")
            return
        self._image.setText("")
        self._image.setPixmap(
            QPixmap.fromImage(image).scaled(
                self._scroll.viewport().size(), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override signature
        super().resizeEvent(event)
        if not self._actual:
            self._render()

    def wait(self) -> None:
        """테스트용: 미리 읽던 사진이 끝날 때까지 기다린다."""
        self._pool.waitForDone()
