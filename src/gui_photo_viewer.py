"""C컷 미리보기에서 사진을 크게 보는 창.

흔들림은 64px 썸네일로는 거의 구분되지 않아, 기준 사진을 고르기 전에 원본을
크게(필요하면 100% 크기로) 확인할 수 있게 한다. ←/→로 표의 현재 정렬 순서대로
넘겨볼 수 있어, 선명도순으로 정렬해두고 넘기다가 "이 정도까지는 괜찮다" 싶은
사진에서 바로 기준을 지정할 수 있다.
"""

from __future__ import annotations

from PIL.ImageQt import ImageQt
from PySide6.QtCore import Qt, Signal
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


class PhotoViewerDialog(QDialog):
    sharpness_reference_chosen = Signal(object)  # ReportRow
    eyes_reference_chosen = Signal(object)  # ReportRow

    def __init__(self, rows: list[ReportRow], index: int, parent=None) -> None:
        super().__init__(parent)
        self._rows = rows
        self._index = index
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

        self._sharpness_ref_button = QPushButton("이 사진을 선명도 기준으로")
        self._sharpness_ref_button.setToolTip(
            "<p>이 사진의 선명도까지는 정상으로 보고, 이보다 선명도가 낮은 사진을 "
            "C컷(블러)으로 판정합니다.</p>"
        )
        self._sharpness_ref_button.clicked.connect(
            lambda: self.sharpness_reference_chosen.emit(self._rows[self._index])
        )
        self._eyes_ref_button = QPushButton("이 사진을 눈뜸 기준으로")
        self._eyes_ref_button.setToolTip(
            "<p>이 사진의 눈 뜬 정도까지는 정상으로 보고, 이보다 눈이 더 감긴 사진을 "
            "C컷(눈감음)으로 판정합니다. 얼굴이 검출된 사진에서만 쓸 수 있습니다.</p>"
        )
        self._eyes_ref_button.clicked.connect(
            lambda: self.eyes_reference_chosen.emit(self._rows[self._index])
        )
        close_button = QPushButton("닫기")
        close_button.clicked.connect(self.accept)

        nav_row = QHBoxLayout()
        nav_row.addWidget(prev_button)
        nav_row.addWidget(next_button)
        nav_row.addWidget(self._actual_size_checkbox)
        nav_row.addStretch()

        action_row = QHBoxLayout()
        action_row.addWidget(self._sharpness_ref_button)
        action_row.addWidget(self._eyes_ref_button)
        action_row.addStretch()
        action_row.addWidget(close_button)

        layout = QVBoxLayout()
        layout.addWidget(self._info_label)
        layout.addWidget(self._scroll, stretch=1)
        layout.addLayout(nav_row)
        layout.addLayout(action_row)
        self.setLayout(layout)

        self._load_current()

    def update_rows(self, rows: list[ReportRow]) -> None:
        """기준을 바꿔 재판정된 결과(판정/사유)를 창에 다시 반영한다. 순서는 같다."""
        self._rows = rows
        self._update_info()

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
        unscored = row.reason.startswith("판정 불가")
        verdict = "C컷" if row.selected else ("판정 불가" if unscored else "정상")
        eyes = "얼굴 없음" if row.face is None else f"{row.face:.2f}"
        region = format_sharpness_region(row.sharpness_region)
        sharpness = f"{row.sharpness:.1f}" + (f" ({region})" if region else "")
        self._info_label.setText(
            f"{row.path.name}  |  판정: {verdict}  |  선명도 {sharpness}  |  "
            f"눈뜸 {eyes}  |  {row.reason}"
        )
        self._sharpness_ref_button.setEnabled(not unscored)
        self._eyes_ref_button.setEnabled(not unscored and row.face is not None)

    def _render(self) -> None:
        if self._pixmap is None:
            self._image_label.setText("이미지를 열 수 없습니다 (RAW 또는 손상된 파일).")
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
