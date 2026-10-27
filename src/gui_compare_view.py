"""같은 장면 컷을 2~4장 나란히 놓고 비교하는 창(베스트컷 화면).

크게 보기 창은 한 장씩 넘겨야 해서 비슷한 연사 컷끼리 초점·표정을 비교하기 어렵다. 이 창은
여러 칸에 한꺼번에 띄우고, 확대·이동을 모든 칸에 똑같이 적용한다(같은 위치를 같은 배율로).
그래서 한 칸에서 눈을 100%로 확대하면 나머지 칸도 같은 자리의 눈을 보여준다.

- 확대: 마우스 휠(커서 위치 기준), 더블클릭(그 자리를 100% ↔ 화면 맞춤), F(맞춤 ↔ 100%)
- 이동: 끌기. 배율은 원본 픽셀 기준이라(1 = 100%) 해상도가 다른 바디가 섞여도 각자 100%다.
- 고르기: 칸 클릭 또는 1~4, ←/→(끝에서 더 가면 장면의 다음·이전 컷으로 밀어 봄)
- 넣기/빼기: Space(뒤집기)·P(넣기)·X(빼기)는 고른 칸의 컷에 한다.
"""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import Callable

from PIL.ImageQt import ImageQt
from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui_settings import load_float, make_settings, save_float
from gui_widgets import FLAGGED_ROW, KEPT_ROW
from report import ReportRow
from thumbnails import load_preview_image

COUNT_SETTINGS_KEY = "bestcut/compare_count"
MIN_COUNT, MAX_COUNT = 2, 4
MAX_ZOOM = 8.0  # 800%
WHEEL_STEP = 1.25
_PIXMAP_CACHE_SIZE = 8  # 원본 크기라 장당 수십 MB — 앞뒤로 넘길 만큼만 들고 있는다
_TITLE_HEIGHT = 26
_INFO_HEIGHT = 22

STATE_KEPT = "kept"
STATE_FLAGGED = "flagged"
STATE_NONE = "none"


class _Panel(QWidget):
    """사진 한 칸. 그리기·마우스는 창(CompareDialog)이 가진 공통 배율·위치를 쓴다."""

    def __init__(self, dialog: "CompareDialog", slot: int) -> None:
        super().__init__()
        self._dialog = dialog
        self.slot = slot
        self.row: ReportRow | None = None
        self.pixmap: QPixmap | None = None
        self._fit_cache: tuple[QSize, QPixmap] | None = None
        self._drag_from: QPointF | None = None
        self.setMinimumSize(200, 200)
        self.setMouseTracking(False)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def set_photo(self, row: ReportRow | None, pixmap: QPixmap | None) -> None:
        if pixmap is not self.pixmap:
            self._fit_cache = None
        self.row, self.pixmap = row, pixmap
        self.update()

    # --- 좌표 ---------------------------------------------------------------------
    def image_rect(self) -> QRectF:
        """사진을 그릴 영역(제목 줄·정보 줄을 뺀 곳)."""
        return QRectF(0, _TITLE_HEIGHT, self.width(), max(1, self.height() - _TITLE_HEIGHT - _INFO_HEIGHT))

    def fit_scale(self) -> float:
        if self.pixmap is None or self.pixmap.isNull():
            return 1.0
        area = self.image_rect()
        return min(area.width() / self.pixmap.width(), area.height() / self.pixmap.height())

    def scale(self) -> float:
        zoom = self._dialog.zoom
        return self.fit_scale() if zoom is None else zoom

    def _origin(self, scale: float) -> QPointF:
        """사진 왼쪽 위 모서리가 놓일 화면 좌표."""
        area = self.image_rect()
        width, height = self.pixmap.width() * scale, self.pixmap.height() * scale
        if self._dialog.zoom is None:
            return QPointF(area.center().x() - width / 2, area.center().y() - height / 2)
        cx, cy = self._dialog.center
        return QPointF(area.center().x() - cx * width, area.center().y() - cy * height)

    def normalized_at(self, point: QPointF) -> tuple[float, float] | None:
        """화면 점 아래의 사진 위치(0~1). 사진이 없으면 None."""
        if self.pixmap is None or self.pixmap.isNull():
            return None
        scale = self.scale()
        origin = self._origin(scale)
        x = (point.x() - origin.x()) / (self.pixmap.width() * scale)
        y = (point.y() - origin.y()) / (self.pixmap.height() * scale)
        return min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0)

    # --- 그리기 -------------------------------------------------------------------
    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override signature
        painter = QPainter(self)
        palette = self.palette()
        painter.fillRect(self.rect(), QColor(30, 30, 30))
        area = self.image_rect()
        if self.pixmap is None or self.pixmap.isNull():
            painter.setPen(QColor(200, 200, 200))
            text = "열 수 없는 사진" if self.row is not None else ""
            painter.drawText(area, Qt.AlignmentFlag.AlignCenter, text)
        else:
            painter.save()
            painter.setClipRect(area)
            scale = self.scale()
            if self._dialog.zoom is None:
                # 화면 맞춤은 줄인 그림을 한 번 만들어 두고 쓴다(끌거나 휠 돌릴 때 빠르게)
                size = QSize(round(self.pixmap.width() * scale), round(self.pixmap.height() * scale))
                if self._fit_cache is None or self._fit_cache[0] != size:
                    self._fit_cache = (
                        size,
                        self.pixmap.scaled(
                            size,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        ),
                    )
                origin = self._origin(scale)
                painter.drawPixmap(origin, self._fit_cache[1])
            else:
                # 보이는 부분만 원본에서 잘라 그린다(큰 사진을 통째로 늘리지 않게)
                origin = self._origin(scale)
                source = QRectF(
                    (area.left() - origin.x()) / scale,
                    (area.top() - origin.y()) / scale,
                    area.width() / scale,
                    area.height() / scale,
                ).intersected(QRectF(0, 0, self.pixmap.width(), self.pixmap.height()))
                target = QRectF(
                    origin.x() + source.left() * scale,
                    origin.y() + source.top() * scale,
                    source.width() * scale,
                    source.height() * scale,
                )
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, scale < 1)
                painter.drawPixmap(target, self.pixmap, source)
            painter.restore()

        if self.row is None:
            return
        state_text, state = self._dialog.state(self.row)
        title = QRectF(0, 0, self.width(), _TITLE_HEIGHT)
        color = {STATE_KEPT: KEPT_ROW, STATE_FLAGGED: FLAGGED_ROW}.get(state)
        painter.fillRect(title, palette.window())
        if color is not None:
            painter.fillRect(title, QColor(color.red(), color.green(), color.blue(), 150))
        font = QFont(self.font())
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(palette.windowText().color())
        painter.drawText(
            title.adjusted(8, 0, -8, 0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            f"{self.slot + 1}. {self.row.path.name}",
        )
        painter.drawText(title.adjusted(8, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, state_text)
        painter.setFont(self.font())
        info = QRectF(0, self.height() - _INFO_HEIGHT, self.width(), _INFO_HEIGHT)
        painter.fillRect(info, palette.window())
        painter.setPen(palette.windowText().color())
        zoom_text = f"{round(self.scale() * 100)}%"
        painter.drawText(info.adjusted(8, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self._dialog.describe(self.row))
        painter.drawText(info.adjusted(8, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, zoom_text)
        if self._dialog.focused_slot == self.slot:
            pen = QPen(palette.highlight().color(), 4)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRectF(self.rect()).adjusted(2, 2, -2, -2))

    # --- 마우스 -------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override signature
        self._dialog.focus_slot(self.slot)
        if event.button() == Qt.MouseButton.LeftButton and self._dialog.zoom is not None:
            self._drag_from = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt override signature
        if self._drag_from is None or self.pixmap is None:
            return
        delta = event.position() - self._drag_from
        self._drag_from = event.position()
        scale = self.scale()
        self._dialog.pan(-delta.x() / (self.pixmap.width() * scale), -delta.y() / (self.pixmap.height() * scale))

    def mouseReleaseEvent(self, _event) -> None:  # noqa: N802 - Qt override signature
        self._drag_from = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt override signature
        point = self.normalized_at(event.position())
        if point is not None:
            self._dialog.toggle_actual_size(point)

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt override signature
        steps = event.angleDelta().y() / 120
        if steps and self.pixmap is not None:
            self._dialog.zoom_at(self, event.position(), WHEEL_STEP**steps)


class CompareDialog(QDialog):
    def __init__(
        self,
        rows: list[ReportRow],
        start: int,
        state: Callable[[ReportRow], tuple[str, str]],
        describe: Callable[[ReportRow], str],
        on_toggle: Callable[[ReportRow], None],
        on_choose: Callable[[ReportRow, bool], None],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.state = state
        self.describe = describe
        self._on_toggle = on_toggle
        self._on_choose = on_choose
        self._rows = rows
        self._settings = make_settings()
        self.zoom: float | None = None  # None = 화면 맞춤, 아니면 원본 픽셀 배율(1 = 100%)
        self.center = (0.5, 0.5)  # 확대했을 때 칸 가운데 오는 사진 위치(0~1)
        self._pixmaps: OrderedDict[Path, QPixmap | None] = OrderedDict()
        saved = int(load_float(self._settings, COUNT_SETTINGS_KEY, 2, MIN_COUNT, MAX_COUNT))
        self._count = max(1, min(saved, len(rows)))
        self._focus = min(max(start, 0), len(rows) - 1)
        self._first = 0
        self._keep_focus_visible()
        self.setWindowTitle("나란히 비교")
        self.resize(1400, 860)

        self._panels = [_Panel(self, slot) for slot in range(MAX_COUNT)]
        panel_row = QHBoxLayout()
        panel_row.setSpacing(6)
        for panel in self._panels:
            panel_row.addWidget(panel, stretch=1)

        self._count_buttons = QButtonGroup(self)
        count_row = QHBoxLayout()
        count_row.addWidget(QLabel("나란히:"))
        for count in range(MIN_COUNT, MAX_COUNT + 1):
            button = QPushButton(f"{count}장")
            button.setCheckable(True)
            button.setEnabled(count <= len(rows))
            button.setChecked(count == self._count)
            self._count_buttons.addButton(button, count)
            count_row.addWidget(button)
        self._count_buttons.idClicked.connect(self._set_count)
        prev_button = QPushButton("◀ 앞 컷")
        prev_button.clicked.connect(lambda: self._slide(-1))
        next_button = QPushButton("다음 컷 ▶")
        next_button.clicked.connect(lambda: self._slide(1))
        fit_button = QPushButton("화면 맞춤")
        fit_button.clicked.connect(self.fit)
        actual_button = QPushButton("100%")
        actual_button.clicked.connect(lambda: self.set_actual_size(self.center))
        self._toggle_button = QPushButton()
        self._toggle_button.clicked.connect(self._toggle_focused)
        self._position_label = QLabel()
        close_button = QPushButton("닫기")
        close_button.clicked.connect(self.accept)

        controls = QHBoxLayout()
        controls.addLayout(count_row)
        controls.addSpacing(16)
        controls.addWidget(prev_button)
        controls.addWidget(next_button)
        controls.addWidget(self._position_label)
        controls.addSpacing(16)
        controls.addWidget(fit_button)
        controls.addWidget(actual_button)
        controls.addStretch()
        controls.addWidget(self._toggle_button)
        controls.addWidget(close_button)
        help_label = QLabel(
            "칸 클릭·1~4 고르기 · ←/→ 옮기기(끝에서 다음 컷) · Space 넣기/빼기 · P 넣기 · X 빼기 · "
            "휠 확대 · 끌어서 이동 · 더블클릭 그 자리 100%/맞춤 · F 맞춤/100% — 확대·이동은 모든 칸 함께"
        )
        help_label.setWordWrap(True)
        # 버튼이 키보드 포커스를 가지면 Space를 버튼이 가져가므로 포커스를 받지 않게 한다
        for button in (*self._count_buttons.buttons(), prev_button, next_button, fit_button,
                       actual_button, self._toggle_button, close_button):
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        for key, action in (
            ("Left", lambda: self._move_focus(-1)),
            ("Right", lambda: self._move_focus(1)),
            ("Space", self._toggle_focused),
            ("P", lambda: self._choose_focused(True)),
            ("X", lambda: self._choose_focused(False)),
            ("F", self._toggle_fit),
            *((str(n), lambda n=n: self.focus_slot(n - 1)) for n in range(1, MAX_COUNT + 1)),
        ):
            QShortcut(QKeySequence(key), self, activated=action)

        layout = QVBoxLayout()
        layout.addLayout(panel_row, stretch=1)
        layout.addLayout(controls)
        layout.addWidget(help_label)
        self.setLayout(layout)
        self._show_window()

    # --- 바깥(베스트컷 화면)에서 --------------------------------------------------------
    @property
    def paths(self) -> list[Path]:
        return [row.path for row in self._rows]

    def update_rows(self, rows: list[ReportRow]) -> None:
        """넣기/빼기·기준 변경으로 판정이 바뀌면 다시 그린다(같은 사진, 같은 순서)."""
        self._rows = rows
        self._show_window()

    @property
    def focused_slot(self) -> int:
        return self._focus - self._first

    def focused_row(self) -> ReportRow:
        return self._rows[self._focus]

    # --- 보이는 컷 ----------------------------------------------------------------------
    def _keep_focus_visible(self) -> None:
        if self._focus < self._first:
            self._first = self._focus
        elif self._focus >= self._first + self._count:
            self._first = self._focus - self._count + 1
        self._first = max(0, min(self._first, len(self._rows) - self._count))

    def _pixmap(self, path: Path) -> QPixmap | None:
        if path in self._pixmaps:
            self._pixmaps.move_to_end(path)
            return self._pixmaps[path]
        image = load_preview_image(path)
        pixmap = None if image is None else QPixmap.fromImage(ImageQt(image.convert("RGBA")))
        self._pixmaps[path] = pixmap
        while len(self._pixmaps) > _PIXMAP_CACHE_SIZE:
            self._pixmaps.popitem(last=False)
        return pixmap

    def _show_window(self) -> None:
        for slot, panel in enumerate(self._panels):
            index = self._first + slot
            visible = slot < self._count and index < len(self._rows)
            panel.setVisible(visible)
            if visible:
                row = self._rows[index]
                panel.set_photo(row, self._pixmap(row.path))
        last = min(self._first + self._count, len(self._rows))
        self._position_label.setText(f"{self._first + 1}–{last} / {len(self._rows)}컷")
        row = self.focused_row()
        self._toggle_button.setText(("이 컷 빼기" if row.selected else "이 컷 넣기") + " (Space)")
        for panel in self._panels:
            panel.update()

    def _set_count(self, count: int) -> None:
        self._count = max(1, min(count, len(self._rows)))
        save_float(self._settings, COUNT_SETTINGS_KEY, count)
        button = self._count_buttons.button(count)
        if button is not None:
            button.setChecked(True)
        self._keep_focus_visible()
        self._show_window()

    def _slide(self, delta: int) -> None:
        self._first = max(0, min(self._first + delta, len(self._rows) - self._count))
        self._focus = min(max(self._focus, self._first), self._first + self._count - 1)
        self._show_window()

    def _move_focus(self, delta: int) -> None:
        self._focus = min(max(self._focus + delta, 0), len(self._rows) - 1)
        self._keep_focus_visible()
        self._show_window()

    def focus_slot(self, slot: int) -> None:
        index = self._first + slot
        if slot < self._count and index < len(self._rows):
            self._focus = index
            self._show_window()

    # --- 넣기/빼기 ---------------------------------------------------------------------
    def _toggle_focused(self) -> None:
        self._on_toggle(self.focused_row())

    def _choose_focused(self, wanted: bool) -> None:
        self._on_choose(self.focused_row(), wanted)

    # --- 확대·이동(모든 칸 함께) ------------------------------------------------------------
    def _redraw(self) -> None:
        for panel in self._panels:
            if panel.isVisible():
                panel.update()

    def fit(self) -> None:
        self.zoom = None
        self.center = (0.5, 0.5)
        self._redraw()

    def set_actual_size(self, point: tuple[float, float]) -> None:
        self.zoom = 1.0
        self.center = point
        self._redraw()

    def toggle_actual_size(self, point: tuple[float, float]) -> None:
        if self.zoom is None:
            self.set_actual_size(point)
        else:
            self.fit()

    def _toggle_fit(self) -> None:
        self.toggle_actual_size(self.center)

    def pan(self, dx: float, dy: float) -> None:
        if self.zoom is None:
            return
        cx, cy = self.center
        self.center = (min(max(cx + dx, 0.0), 1.0), min(max(cy + dy, 0.0), 1.0))
        self._redraw()

    def zoom_at(self, panel: _Panel, position: QPointF, factor: float) -> None:
        """커서 아래 지점이 그 자리에 머물도록 배율을 바꾼다. 화면 맞춤보다 작아지면 맞춤으로."""
        point = panel.normalized_at(position)
        if point is None:
            return
        fit = panel.fit_scale()
        new_zoom = min((self.zoom if self.zoom is not None else fit) * factor, MAX_ZOOM)
        if new_zoom <= fit:
            self.fit()
            return
        area = panel.image_rect()
        width = panel.pixmap.width() * new_zoom
        height = panel.pixmap.height() * new_zoom
        self.zoom = new_zoom
        self.center = (
            min(max(point[0] - (position.x() - area.center().x()) / width, 0.0), 1.0),
            min(max(point[1] - (position.y() - area.center().y()) / height, 0.0), 1.0),
        )
        self._redraw()
