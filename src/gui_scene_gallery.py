"""베스트컷 결과를 장면별로 큰 썸네일로 늘어놓는 갤러리 보기.

표는 숫자를 비교하기엔 좋지만 사진을 고르기엔 썸네일이 작고 장면 경계가 잘 안 보인다.
갤러리는 장면마다 한 줄(사진이 많으면 여러 줄로 접힘)에 큰 썸네일을 놓고, 남기는 컷은
초록 테두리와 ✓, 빠지는 컷은 흐리게 그린다.

장면이 수백 개여도 가볍도록 위젯을 장면마다 만들지 않고, 장면 하나를 목록의 항목 하나로
두고 직접 그린다(보이는 장면만 그림). 클릭한 사진은 표의 같은 줄과 맞춰(row_index) 탭의
기존 버튼(크게 보기·넣기/빼기)이 그대로 동작한다.

조작: 클릭 = 고르기, 더블클릭/Enter = 크게 보기, Space = 넣기/빼기, P = 넣기,
X = 빼기, ←/→ = 앞뒤 사진, ↑/↓ = 앞뒤 장면.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QAbstractItemView, QListView, QStyledItemDelegate

THUMB = 150  # 썸네일 칸 한 변
CELL_W = THUMB + 16
CELL_H = THUMB + 40  # 썸네일 + 파일 이름·점수 두 줄
HEADER_H = 30
MARGIN = 8
_SCENE_ROLE = Qt.ItemDataRole.UserRole + 1

KEPT_BORDER = QColor(52, 168, 83)


@dataclass(frozen=True)
class GalleryPhoto:
    row_index: int  # 결과 표에서의 줄 번호
    path: Path
    selected: bool
    manual: bool  # 직접 넣거나 뺌
    total: float


@dataclass(frozen=True)
class GalleryScene:
    scene_id: int
    photos: tuple[GalleryPhoto, ...]


def _per_row(width: int) -> int:
    return max(1, (width - 2 * MARGIN) // CELL_W)


def _cell_rects(rect: QRect, count: int) -> list[QRect]:
    per_row = _per_row(rect.width())
    return [
        QRect(
            rect.x() + MARGIN + (i % per_row) * CELL_W,
            rect.y() + HEADER_H + (i // per_row) * CELL_H,
            CELL_W,
            CELL_H,
        )
        for i in range(count)
    ]


class _SceneDelegate(QStyledItemDelegate):
    def __init__(self, view: SceneGallery) -> None:
        super().__init__(view)
        self._view = view

    def sizeHint(self, option, index) -> QSize:  # noqa: N802 - Qt override signature
        scene: GalleryScene = index.data(_SCENE_ROLE)
        width = max(CELL_W + 2 * MARGIN, self._view.viewport().width() - 2)
        rows = math.ceil(len(scene.photos) / _per_row(width))
        return QSize(width, HEADER_H + rows * CELL_H + MARGIN)

    def paint(self, painter: QPainter, option, index) -> None:
        scene: GalleryScene = index.data(_SCENE_ROLE)
        rect = option.rect
        palette = self._view.palette()
        painter.save()

        kept = sum(1 for p in scene.photos if p.selected)
        header = QRect(rect.x() + MARGIN, rect.y() + 4, rect.width() - 2 * MARGIN, HEADER_H - 8)
        font = QFont(painter.font())
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(palette.text().color())
        painter.drawText(
            header,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            f"장면 {scene.scene_id}   ·   {len(scene.photos)}장 중 {kept}장 남김",
        )
        painter.setPen(QPen(palette.mid().color(), 1))
        painter.drawLine(header.left(), rect.y() + 1, header.right(), rect.y() + 1)

        small = QFont(painter.font())
        small.setBold(False)
        small.setPointSizeF(max(8.0, small.pointSizeF() - 1))
        for photo, cell in zip(scene.photos, _cell_rects(rect, len(scene.photos))):
            self._paint_photo(painter, photo, cell, small, palette)
        painter.restore()

    def _paint_photo(self, painter: QPainter, photo: GalleryPhoto, cell: QRect, font, palette) -> None:
        box = QRect(cell.x() + 4, cell.y() + 4, THUMB, THUMB)
        current = photo.row_index == self._view.current_row()
        pixmap = self._view.pixmap_for(photo.path, THUMB)

        painter.setOpacity(1.0 if photo.selected else 0.55)
        if pixmap is not None and not pixmap.isNull():
            scaled = pixmap.scaled(
                box.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
            painter.drawPixmap(
                box.x() + (box.width() - scaled.width()) // 2,
                box.y() + (box.height() - scaled.height()) // 2,
                scaled,
            )
        else:
            painter.fillRect(box, palette.midlight())
            painter.setPen(palette.text().color())
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, "미리보기 없음")
        painter.setOpacity(1.0)

        if photo.selected:
            painter.setPen(QPen(KEPT_BORDER, 3))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(box.adjusted(1, 1, -1, -1), 4, 4)
            badge = QRect(box.x() + 6, box.y() + 6, 22, 22)
            painter.setBrush(KEPT_BORDER)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(badge)
            painter.setPen(QColor("white"))
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, "✓")
        if current:
            painter.setPen(QPen(palette.highlight().color(), 3))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(box.adjusted(-3, -3, 3, 3), 6, 6)

        painter.setFont(font)
        painter.setPen(palette.text().color())
        metrics = painter.fontMetrics()
        name_rect = QRect(cell.x() + 4, box.bottom() + 4, THUMB, metrics.height())
        painter.drawText(
            name_rect,
            Qt.AlignmentFlag.AlignHCenter,
            metrics.elidedText(photo.path.name, Qt.TextElideMode.ElideMiddle, THUMB),
        )
        detail = f"종합 {photo.total:.2f}" + ("  · 직접" if photo.manual else "")
        painter.setPen(palette.placeholderText().color())
        painter.drawText(name_rect.translated(0, metrics.height()), Qt.AlignmentFlag.AlignHCenter, detail)


class SceneGallery(QListView):
    photo_clicked = Signal(int)  # 결과 표 줄 번호
    photo_activated = Signal(int)
    toggle_requested = Signal(int)
    choice_requested = Signal(int, bool)  # P = 넣기(True), X = 빼기(False)

    def __init__(self, pixmap_for: Callable[[Path, int], QPixmap | None]) -> None:
        super().__init__()
        self.pixmap_for = pixmap_for
        self._model = QStandardItemModel(self)
        self.setModel(self._model)
        self.setItemDelegate(_SceneDelegate(self))
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(24)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setUniformItemSizes(False)
        self._scenes: list[GalleryScene] = []
        self._order: list[int] = []  # 화면 순서대로의 row_index
        self._current: int | None = None

    # --- 데이터 ---------------------------------------------------------------
    def set_scenes(self, scenes: list[GalleryScene]) -> None:
        scroll = self.verticalScrollBar().value()
        self._scenes = scenes
        self._order = [p.row_index for s in scenes for p in s.photos]
        self._model.clear()
        for scene in scenes:
            item = QStandardItem()
            item.setData(scene, _SCENE_ROLE)
            item.setEditable(False)
            self._model.appendRow(item)
        if self._current not in self._order:
            self._current = None
        self.verticalScrollBar().setValue(scroll)

    def current_row(self) -> int | None:
        return self._current

    def set_current(self, row_index: int | None, scroll: bool = True) -> None:
        """표에서 고른 줄을 갤러리에도 표시한다(신호는 내지 않음)."""
        if row_index == self._current:
            return
        self._current = row_index
        if scroll and row_index is not None:
            self._scroll_to_photo(row_index)
        self.viewport().update()

    # --- 위치 계산 --------------------------------------------------------------
    def _scene_index_of(self, row_index: int) -> int | None:
        for i, scene in enumerate(self._scenes):
            if any(p.row_index == row_index for p in scene.photos):
                return i
        return None

    def _photo_at(self, pos: QPoint) -> GalleryPhoto | None:
        index = self.indexAt(pos)
        if not index.isValid():
            return None
        scene: GalleryScene = index.data(_SCENE_ROLE)
        for photo, cell in zip(scene.photos, _cell_rects(self.visualRect(index), len(scene.photos))):
            if cell.contains(pos):
                return photo
        return None

    def _scroll_to_photo(self, row_index: int) -> None:
        scene_index = self._scene_index_of(row_index)
        if scene_index is None:
            return
        index = self._model.index(scene_index, 0)
        scene = self._scenes[scene_index]
        cells = _cell_rects(self.visualRect(index), len(scene.photos))
        for photo, cell in zip(scene.photos, cells):
            if photo.row_index == row_index:
                viewport = self.viewport().rect()
                if cell.top() < viewport.top() or cell.bottom() > viewport.bottom():
                    bar = self.verticalScrollBar()
                    bar.setValue(bar.value() + cell.top() - HEADER_H - viewport.top())
                return

    # --- 마우스·키보드 ------------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override signature
        photo = self._photo_at(event.position().toPoint())
        self.setFocus()
        if photo is not None:
            self._choose(photo.row_index, scroll=False)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt override signature
        photo = self._photo_at(event.position().toPoint())
        if photo is not None:
            self._choose(photo.row_index, scroll=False)
            self.photo_activated.emit(photo.row_index)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt override signature
        key = event.key()
        if not self._order:
            return super().keyPressEvent(event)
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            position = self._order.index(self._current) if self._current in self._order else -1
            step = 1 if key == Qt.Key.Key_Right else -1
            target = min(max(position + step, 0), len(self._order) - 1)
            self._choose(self._order[target])
        elif key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            scene_index = self._scene_index_of(self._current) if self._current is not None else -1
            step = 1 if key == Qt.Key.Key_Down else -1
            target = min(max((scene_index if scene_index is not None else -1) + step, 0), len(self._scenes) - 1)
            self._choose(self._scenes[target].photos[0].row_index)
        elif key == Qt.Key.Key_Space and self._current is not None:
            self.toggle_requested.emit(self._current)
        elif key in (Qt.Key.Key_P, Qt.Key.Key_X) and self._current is not None:
            self.choice_requested.emit(self._current, key == Qt.Key.Key_P)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self._current is not None:
            self.photo_activated.emit(self._current)
        else:
            super().keyPressEvent(event)

    def _choose(self, row_index: int, scroll: bool = True) -> None:
        self._current = row_index
        if scroll:
            self._scroll_to_photo(row_index)
        self.viewport().update()
        self.photo_clicked.emit(row_index)
