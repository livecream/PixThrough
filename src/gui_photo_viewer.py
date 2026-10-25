"""미리보기 표의 사진을 크게 보는 창 (C컷 정리·베스트컷 선별 탭 공용).

흔들림은 64px 썸네일로는 거의 구분되지 않아, 판단하기 전에 원본을 크게(필요하면
100% 크기로) 확인할 수 있게 한다. ←/→로 넘겨볼 사진 목록과 아래쪽 버튼(무엇을 할지)은
여는 탭이 정한다 — C컷 탭은 "이 사진을 기준으로", 베스트컷 탭은 같은 장면 컷끼리
넘기며 "이 컷 선택/빼기"를 넘긴다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PIL.ImageQt import ImageQt
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
)

from gui_format import format_sharpness_region
from report import ReportRow
from thumbnails import load_preview_image


@dataclass(frozen=True)
class ViewerAction:
    """크게 보기 창 아래쪽 버튼 하나. 글자·활성 여부는 지금 보는 사진에 따라 바뀔 수 있다."""

    label: Callable[[ReportRow], str]
    tooltip: str
    enabled: Callable[[ReportRow], bool]
    triggered: Callable[[ReportRow], None]
    shortcut: str | None = None  # 예: "Space". 버튼 글자 옆에 함께 보여준다.
    shortcut_help: str = ""  # 아래 안내 줄에 쓸 짧은 설명 (예: "넣기/빼기")


@dataclass(frozen=True)
class ViewerShortcut:
    """버튼 없이 키로만 하는 동작 (예: P = 넣기, X = 빼기). 창 아래 안내 줄에 보여준다."""

    key: str
    description: str
    triggered: Callable[[ReportRow], None]


class PhotoViewerDialog(QDialog):
    def __init__(
        self,
        rows: list[ReportRow],
        index: int,
        actions: list[ViewerAction],
        verdict: Callable[[ReportRow], str],
        parent=None,
        shortcuts: list[ViewerShortcut] | None = None,
    ) -> None:
        super().__init__(parent)
        self._rows = rows
        self._index = index
        self._actions = actions
        self._verdict = verdict
        self._pixmap: QPixmap | None = None
        self.resize(1100, 800)

        self._info_label = QLabel()
        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scroll = QScrollArea()
        self._scroll.setWidget(self._image_label)
        self._scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._actual_size_checkbox = QCheckBox("실제 크기(100%)로 보기")
        self._actual_size_checkbox.setToolTip(
            "<p>화면에 맞춰 줄이면 흔들림이 가려져 보일 수 있습니다. "
            "체크하면 원본 픽셀 그대로 보여주니, 스크롤해서 눈·머리카락 같은 "
            "디테일로 초점을 확인하세요.</p>"
        )
        self._actual_size_checkbox.toggled.connect(self._render)

        prev_button = QPushButton("← 이전")
        prev_button.clicked.connect(lambda: self._step(-1))
        next_button = QPushButton("다음 →")
        next_button.clicked.connect(lambda: self._step(1))
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, activated=lambda: self._step(-1))
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, activated=lambda: self._step(1))

        self._action_buttons: list[QPushButton] = []
        for action in actions:
            button = QPushButton()
            button.setToolTip(action.tooltip)
            button.clicked.connect(
                lambda _checked=False, action=action: action.triggered(self._rows[self._index])
            )
            self._action_buttons.append(button)
            if action.shortcut:
                QShortcut(
                    QKeySequence(action.shortcut),
                    self,
                    activated=lambda action=action: self._trigger_if_enabled(action),
                )
        shortcut_help = ["←/→ 넘기기"]
        shortcut_help += [f"{a.shortcut} {a.shortcut_help}" for a in actions if a.shortcut]
        for extra in shortcuts or []:
            QShortcut(
                QKeySequence(extra.key),
                self,
                activated=lambda extra=extra: extra.triggered(self._rows[self._index]),
            )
            shortcut_help.append(f"{extra.key} {extra.description}")
        self._shortcut_label = QLabel("단축키: " + " · ".join(shortcut_help))
        close_button = QPushButton("닫기")
        close_button.clicked.connect(self.accept)
        # 버튼이 키보드 포커스를 가지면 스페이스를 그 버튼이 먼저 가져가(누름 처리) 단축키가
        # 엉뚱하게 동작하므로, 이 창의 버튼·체크박스는 포커스를 받지 않게 한다.
        for widget in (
            prev_button,
            next_button,
            self._actual_size_checkbox,
            close_button,
            *self._action_buttons,
        ):
            widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        nav_row = QHBoxLayout()
        nav_row.addWidget(prev_button)
        nav_row.addWidget(next_button)
        nav_row.addWidget(self._actual_size_checkbox)
        nav_row.addStretch()

        action_row = QHBoxLayout()
        for button in self._action_buttons:
            action_row.addWidget(button)
        action_row.addStretch()
        action_row.addWidget(close_button)

        layout = QVBoxLayout()
        layout.addWidget(self._info_label)
        layout.addWidget(self._scroll, stretch=1)
        layout.addLayout(nav_row)
        layout.addLayout(action_row)
        layout.addWidget(self._shortcut_label)
        self.setLayout(layout)

        self._load_current()

    def update_rows(self, rows: list[ReportRow]) -> None:
        """다시 판정/선택된 결과(판정·사유)를 창에 다시 반영한다. 순서는 같다."""
        self._rows = rows
        self._update_info()

    def _trigger_if_enabled(self, action: ViewerAction) -> None:
        row = self._rows[self._index]
        if action.enabled(row):
            action.triggered(row)

    def _step(self, delta: int) -> None:
        new_index = self._index + delta
        if 0 <= new_index < len(self._rows):
            self._index = new_index
            self._load_current()

    def _load_current(self) -> None:
        row = self._rows[self._index]
        self.setWindowTitle(f"{row.path.name} ({self._index + 1}/{len(self._rows)})")
        img = load_preview_image(row.path)
        self._pixmap = None if img is None else QPixmap.fromImage(ImageQt(img.convert("RGBA")))
        self._update_info()
        self._render()

    def _update_info(self) -> None:
        row = self._rows[self._index]
        eyes = "해당 없음" if row.face is None else f"{row.face:.2f}"
        region = format_sharpness_region(row.sharpness_region)
        sharpness = f"{row.sharpness:.1f}" + (f" ({region})" if region else "")
        self._info_label.setText(
            f"{row.path.name}  |  {self._verdict(row)}  |  선명도 {sharpness}  |  "
            f"눈뜸 {eyes}  |  {row.reason}"
        )
        for action, button in zip(self._actions, self._action_buttons):
            label = action.label(row)
            button.setText(f"{label} ({action.shortcut})" if action.shortcut else label)
            button.setEnabled(action.enabled(row))

    def _render(self) -> None:
        if self._pixmap is None:
            self._image_label.setText("이미지를 열 수 없습니다 (미리보기가 없는 RAW 또는 손상된 파일).")
            self._image_label.adjustSize()
            return
        if self._actual_size_checkbox.isChecked():
            shown = self._pixmap
            self._scroll.setWidgetResizable(False)
        else:
            shown = self._pixmap.scaled(
                self._scroll.viewport().size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._scroll.setWidgetResizable(True)
        self._image_label.setPixmap(shown)
        self._image_label.adjustSize()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override signature
        super().resizeEvent(event)
        if not self._actual_size_checkbox.isChecked():
            self._render()
