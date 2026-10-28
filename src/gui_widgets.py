"""여러 탭이 같이 쓰는 작은 화면 부품: 작은 폴더 목록, 강조 버튼, 경로 표시, 상태줄.

탭마다 같은 모양을 쓰도록 모아 둔 것으로, 로직은 없다.
- FolderList: 몇 줄만 차지하는 입력 폴더 목록. 비어 있으면 끌어다 놓으라는 안내를 그린다.
- make_primary: 탭에서 가장 중요한 버튼(미리보기·적용)을 시스템 강조색으로 칠한다.
- PathLabel: 긴 경로는 가운데를 …로 줄여 폴더 이름(끝부분)이 보이게 한다. 전체는 툴팁.
- SummaryLabel: 결과 요약 몇 줄. 예전 큰 텍스트 칸(setPlainText)을 그대로 대신한다.
- status_row: 진행률 막대와 상태 글자를 한 줄로.
- EtaProgressBar: 진행률 막대에 남은 시간을 함께 쓰고, 오래 걸린 작업이 끝나면(앱을 보고
  있지 않을 때) Dock 아이콘을 튀기거나 작업 표시줄을 깜빡여 알린다.
"""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStyledItemDelegate,
    QTableWidget,
    QToolButton,
    QVBoxLayout,
)

FOLDER_LIST_ROWS = 3
PROGRESS_WIDTH = 160

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
    fit_text(button)
    return button


def fit_text(button: QPushButton, padding: int = 44) -> None:
    """스타일시트로 안쪽 여백을 준 버튼은 Qt가 계산한 크기에 그 여백이 빠져 글자가 잘릴 수
    있어, 지금 글자 폭 + 여백을 최소 폭으로 둔다(글자를 바꾼 뒤 다시 부를 것)."""
    button.setMinimumWidth(max(110, button.fontMetrics().horizontalAdvance(button.text()) + padding))


# 되돌리기 어려운 동작(이동·삭제) 버튼: 글자만 빨갛게(배경은 OS 기본이라 모양이 어색하지 않게)
_DANGER_STYLE = "QPushButton:enabled { color: rgb(200, 40, 40); font-weight: 600; }"


def make_danger(button: QPushButton) -> QPushButton:
    button.setStyleSheet(_DANGER_STYLE)
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

    def set_text(self, text: str) -> None:
        """경로가 아닌 글자도 같은 방식(공간이 모자라면 가운데 …, 전체는 툴팁)으로."""
        self._full = text
        self.setToolTip(text)
        self._update_elided()

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


ETA_MIN_SECONDS = 3.0  # 이만큼 지나야 남은 시간을 보여준다(처음 몇 장은 들쭉날쭉)
NOTIFY_AFTER_SECONDS = 15.0  # 이보다 오래 걸린 작업이 끝나면 알린다
_NOTIFY_SETTLE_MS = 1500  # 단계가 이어지는 작업(스캔 → 채점)은 다음 단계가 시작되면 알리지 않는다


def format_remaining(seconds: float) -> str:
    """남은 시간을 대략적으로: "약 10초", "약 2분", "약 1시간 5분"."""
    if seconds < 60:
        return f"약 {max(5, int(round(seconds / 5)) * 5)}초"
    minutes = int(round(seconds / 60))
    if minutes < 60:
        return f"약 {minutes}분"
    return f"약 {minutes // 60}시간 {minutes % 60}분" if minutes % 60 else f"약 {minutes // 60}시간"


class EtaProgressBar(QProgressBar):
    """setValue(0)이나 setMaximum(다른 값)으로 새 작업(단계)이 시작된 것으로 보고 시간을 잰다.

    지금까지의 속도로 남은 시간을 막대 옆 글자(eta_label, status_row가 붙임 — macOS 기본 모양은
    막대 안에 글자를 그리지 않아서)에 "약 2분 남음"으로 보여주고, 끝(값 = 최대)에
    닿은 뒤 잠시 다음 단계가 없고 전체가 NOTIFY_AFTER_SECONDS보다 오래 걸렸으면 창이 활성이
    아닐 때 QApplication.alert로 알린다."""

    def __init__(self) -> None:
        super().__init__()
        self._stage_start = time.monotonic()
        self._task_start: float | None = None  # 여러 단계를 합친 작업 시작(알림 판단용)
        self._notify_timer = QTimer(self)
        self._notify_timer.setSingleShot(True)
        self._notify_timer.setInterval(_NOTIFY_SETTLE_MS)
        self._notify_timer.timeout.connect(self._notify_if_done)
        self.notified = 0  # 테스트용: 알린 횟수
        self.eta_label = QLabel()

    def _restart_stage(self) -> None:
        now = time.monotonic()
        # 앞 단계가 막 끝났거나(알림 대기 중) 방금 시작한 단계면 같은 작업의 다음 단계로 본다.
        # 그 밖에는 새 작업(앞 작업이 취소·실패로 끝에 닿지 않았어도 여기서 새로 잰다).
        continuing = self._notify_timer.isActive() or now - self._stage_start < 0.2
        if not continuing or self._task_start is None:
            self._task_start = now
        self._notify_timer.stop()
        self._stage_start = now

    def setMaximum(self, maximum: int) -> None:  # noqa: N802 - Qt override signature
        if maximum != self.maximum():
            self._restart_stage()
        super().setMaximum(maximum)
        self._update_format()

    def setValue(self, value: int) -> None:  # noqa: N802 - Qt override signature
        if value == 0:
            self._restart_stage()
        super().setValue(value)
        self._update_format()
        if self.maximum() > 0 and value >= self.maximum():
            self._notify_timer.start()

    def _update_format(self) -> None:
        value, maximum = self.value(), self.maximum()
        elapsed = time.monotonic() - self._stage_start
        # 처음 몇 장(모델 불러오기·여러 장 동시 처리 시작)은 속도가 들쭉날쭉해 5%는 지난 뒤부터
        if 0 < value < maximum and elapsed >= ETA_MIN_SECONDS and value >= max(3, maximum * 0.05):
            remaining = elapsed / value * (maximum - value)
            self.eta_label.setText(f"{format_remaining(remaining)} 남음")
        else:
            self.eta_label.setText("")
        self.setToolTip(f"{value} / {maximum}" if maximum > 0 and value < maximum else "")

    def _notify_if_done(self) -> None:
        start, self._task_start = self._task_start, None
        if start is None or time.monotonic() - start < NOTIFY_AFTER_SECONDS:
            return
        window = self.window()
        if not window.isActiveWindow():
            QApplication.alert(window, 0)  # macOS Dock 튀기기 / Windows 작업 표시줄 깜빡임
            self.notified += 1


def status_row(progress_bar: QProgressBar, status_label: QLabel) -> QHBoxLayout:
    progress_bar.setFixedWidth(PROGRESS_WIDTH)
    progress_bar.setMaximumHeight(16)
    # 상태 글자가 길어도 옆 버튼을 밀어내거나 창을 넓히지 않게(남는 폭만큼만 보임, 전체는 툴팁)
    status_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    status_label.setMinimumWidth(40)
    row = QHBoxLayout()
    row.addWidget(progress_bar)
    if isinstance(progress_bar, EtaProgressBar):
        row.addWidget(progress_bar.eta_label)
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


# 표 칸 아래쪽에 0~1 값을 가는 막대로 그린다(숫자만으로는 비교가 어려운 점수 칸용).
BAR_ROLE = Qt.ItemDataRole.UserRole + 20
_BAR_HEIGHT = 4


class BarDelegate(QStyledItemDelegate):
    """칸 글자는 그대로 두고, BAR_ROLE(0~1) 값이 있으면 칸 아래쪽에 막대를 그린다."""

    def paint(self, painter, option, index) -> None:
        super().paint(painter, option, index)
        fraction = index.data(BAR_ROLE)
        if fraction is None:
            return
        fraction = min(1.0, max(0.0, float(fraction)))
        rect = option.rect.adjusted(6, 0, -6, -6)
        track = QRect(rect.left(), rect.bottom() - _BAR_HEIGHT, rect.width(), _BAR_HEIGHT)
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(128, 128, 128, 50))
        painter.drawRoundedRect(track, 2, 2)
        if fraction > 0:
            painter.setBrush(option.palette.highlight())
            painter.drawRoundedRect(QRect(track.left(), track.top(), max(2, round(track.width() * fraction)), _BAR_HEIGHT), 2, 2)
        painter.restore()


class WarningLabel(QLabel):
    """설정 칸 아래 빨간 경고 한두 줄(비면 숨음). 폴더가 겹치는 것처럼 실행 전에 알 수 있는 문제용."""

    def __init__(self) -> None:
        super().__init__()
        self.setWordWrap(True)
        self.setStyleSheet("color: rgb(200, 40, 40);")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.set_warning("")

    def set_warning(self, text: str) -> None:
        self.setText(text)
        self.setVisible(bool(text))


class GuidePanel(QFrame):
    """결과가 아직 없을 때 결과 자리에 보여주는 순서 안내. 끝난 단계는 ✓로 바뀐다."""

    def __init__(self, title: str, steps: list[str]) -> None:
        super().__init__()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        heading = QLabel(title)
        font = QFont(heading.font())
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() * 1.15)
        heading.setFont(font)
        self._steps = steps
        self._labels = [QLabel() for _ in steps]
        column = QVBoxLayout()
        column.addStretch()
        column.addWidget(heading)
        column.addSpacing(8)
        for label in self._labels:
            label.setWordWrap(True)
            column.addWidget(label)
        column.addStretch()
        row = QHBoxLayout()
        row.addStretch(1)
        row.addLayout(column, stretch=2)
        row.addStretch(1)
        self.setLayout(row)
        self.set_done([False] * len(steps))

    def set_done(self, done: list[bool]) -> None:
        for i, (label, text, finished) in enumerate(zip(self._labels, self._steps, done), start=1):
            mark = "✓" if finished else f"{i}."
            label.setText(f"{mark}  {text}")
            label.setEnabled(not finished)  # 끝난 단계는 흐리게
