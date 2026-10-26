"""여러 탭이 같이 쓰는 작은 화면 부품: 작은 폴더 목록, 강조 버튼, 경로 표시, 상태줄.

탭마다 같은 모양을 쓰도록 모아 둔 것으로, 로직은 없다.
- FolderList: 몇 줄만 차지하는 입력 폴더 목록. 비어 있으면 끌어다 놓으라는 안내를 그린다.
- make_primary: 탭에서 가장 중요한 버튼(미리보기·적용)을 시스템 강조색으로 칠한다.
- PathLabel: 긴 경로는 가운데를 …로 줄여 폴더 이름(끝부분)이 보이게 한다. 전체는 툴팁.
- SummaryLabel: 결과 요약 몇 줄. 예전 큰 텍스트 칸(setPlainText)을 그대로 대신한다.
- status_row: 진행률 막대와 상태 글자를 한 줄로.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStyledItemDelegate,
    QTableWidget,
    QToolButton,
)

FOLDER_LIST_ROWS = 3
PROGRESS_WIDTH = 240

_PRIMARY_STYLE = """
QPushButton {
    background-color: palette(highlight);
    color: palette(highlighted-text);
    border: none;
    border-radius: 6px;
    padding: 6px 18px;
    font-weight: 600;
}
QPushButton:hover { background-color: palette(highlight); }
QPushButton:pressed { padding-top: 7px; padding-bottom: 5px; }
QPushButton:disabled { background-color: palette(mid); color: palette(light); }
"""


class _ElidedItemDelegate(QStyledItemDelegate):
    """항목 폭을 목록 폭에 맞춰(가로 스크롤 없이) 긴 경로는 가운데를 …로 줄여 그리게 한다."""

    def sizeHint(self, option, index) -> QSize:  # noqa: N802 - Qt override signature
        hint = super().sizeHint(option, index)
        return QSize(1, hint.height())


class FolderList(QListWidget):
    """입력 폴더 목록. 몇 줄 높이만 차지하고, 비어 있으면 안내 문구를 가운데 그린다."""

    def __init__(
        self,
        placeholder: str = "폴더를 여기로 끌어다 놓거나 + 버튼으로 추가하세요",
        rows: int = FOLDER_LIST_ROWS,
    ) -> None:
        super().__init__()
        self._placeholder = placeholder
        self._rows = rows
        self.setAcceptDrops(False)  # 끌어다 놓기는 탭 전체가 받는다
        self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setItemDelegate(_ElidedItemDelegate(self))
        # 줄인 경로의 전체는 마우스를 올리면 보이게
        self.model().rowsInserted.connect(self._set_tooltips)

    def _set_tooltips(self, _parent, first: int, last: int) -> None:
        for row in range(first, last + 1):
            item = self.item(row)
            if item is not None:
                item.setToolTip(item.text())

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override signature
        row = self.fontMetrics().height() + 6
        return QSize(super().sizeHint().width(), row * self._rows + 2 * self.frameWidth() + 4)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override signature
        # 스크롤 영역 기본 최소 높이가 줄 수 지정보다 커서, 같은 높이로 맞춘다
        return QSize(super().minimumSizeHint().width(), self.sizeHint().height())

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override signature
        super().paintEvent(event)
        if self.count() == 0:
            painter = QPainter(self.viewport())
            painter.setPen(self.palette().placeholderText().color())
            painter.drawText(
                self.viewport().rect().adjusted(8, 0, -8, 0),
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                self._placeholder,
            )


def small_button(text: str, tooltip: str = "") -> QToolButton:
    """+ / − 처럼 짧은 글자만 쓰는 작은 버튼."""
    button = QToolButton()
    button.setText(text)
    button.setToolTip(tooltip)
    button.setAutoRaise(False)
    button.setMinimumWidth(28)
    return button


def make_primary(button: QPushButton) -> QPushButton:
    button.setStyleSheet(_PRIMARY_STYLE)
    button.setMinimumWidth(110)
    return button


def compact(button: QPushButton) -> QPushButton:
    """줄 전체로 늘어나지 않고 글자만큼만 차지하게 한다."""
    button.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    return button


class PathLabel(QLabel):
    """경로 표시. 공간이 모자라면 가운데를 …로 줄이고, 전체 경로는 툴팁으로 보여준다."""

    def __init__(self, empty_text: str = "(선택 안 됨)") -> None:
        super().__init__()
        self._empty_text = empty_text
        self._full = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.set_path(None)

    def set_path(self, path: Path | None) -> None:
        self._full = str(path) if path is not None else ""
        self.setToolTip(self._full)
        self._update_elided()

    def path_text(self) -> str:
        return self._full

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override signature
        super().resizeEvent(event)
        self._update_elided()

    def _update_elided(self) -> None:
        if not self._full:
            super().setText(self._empty_text)
            return
        super().setText(
            self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideMiddle, max(40, self.width()))
        )


class SummaryLabel(QLabel):
    """결과 요약 몇 줄. 예전 텍스트 칸과 같은 setPlainText로 쓴다."""

    def __init__(self) -> None:
        super().__init__()
        self.setWordWrap(True)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

    def setPlainText(self, text: str) -> None:  # noqa: N802 - QPlainTextEdit과 같은 이름
        self.setText(text)

    def toPlainText(self) -> str:  # noqa: N802
        return self.text()


def status_row(progress_bar: QProgressBar, status_label: QLabel) -> QHBoxLayout:
    progress_bar.setFixedWidth(PROGRESS_WIDTH)
    progress_bar.setMaximumHeight(16)
    row = QHBoxLayout()
    row.addWidget(progress_bar)
    row.addWidget(status_label, stretch=1)
    return row


# 결과 표 줄 색. 반투명이라 밝은/어두운 테마 모두에서 글자가 그대로 읽힌다.
SCENE_BAND = QColor(128, 128, 128, 28)  # 장면을 하나 걸러 옅은 회색
KEPT_ROW = QColor(52, 168, 83, 70)  # 남기는 컷(베스트컷 선택)
FLAGGED_ROW = QColor(234, 67, 53, 60)  # 걸러낼 컷(C컷)


def paint_row(table: QTableWidget, row: int, color: QColor | None, bold_column: int | None = None) -> None:
    """한 줄 전체 배경을 칠한다(None이면 기본 배경). bold_column 칸은 굵게."""
    brush = QBrush(color) if color is not None else QBrush()
    for column in range(table.columnCount()):
        item = table.item(row, column)
        if item is None:
            continue
        item.setBackground(brush)
        if column == bold_column:
            font = QFont(item.font())
            font.setBold(color is not None)
            item.setFont(font)
