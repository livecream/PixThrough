"""바디별 시계 맞추기 창과, 두 화면(시간순 정렬·베스트컷)이 같이 쓰는 시차 저장소.

- ClockOffsetStore: 바디 이름 → 시차(초)를 QSettings(`clock/offsets`)에 기억하고, 바뀌면
  changed 신호로 두 화면에 알린다(베스트컷은 즉시 다시 묶고, 시간순 정렬은 다시 미리보기).
- ClockSyncDialog: 입력 폴더의 사진을 읽어 바디(기종+시리얼)마다 사진 수·첫/마지막 시각을
  보여주고, 시차를 직접 넣거나 "같은 순간을 찍은 두 사진"으로 계산해 넣는다.
원본 파일의 촬영 시각은 바꾸지 않는다(clock_sync).
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtGui import QPixmap, QValidator
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

import app_log
from clock_sync import (
    MAX_OFFSET_SECONDS,
    CameraInfo,
    camera_key,
    format_offset,
    offset_from_pair,
    parse_offset,
    summarize,
)
from gui_settings import make_settings
from scanner import PhotoMetadata, extract_metadata, scan_and_extract_many
from thumbnails import make_thumbnail_jpeg

log = app_log.get("clock")

SETTINGS_KEY = "clock/offsets"


class ClockOffsetStore(QObject):
    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._settings = make_settings()

    def get(self) -> dict[str, float]:
        try:
            data = json.loads(self._settings.value(SETTINGS_KEY, "{}") or "{}")
        except (TypeError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(k): float(v) for k, v in data.items() if isinstance(v, (int, float)) and v}

    def set(self, offsets: dict[str, float]) -> None:
        cleaned = {k: float(v) for k, v in offsets.items() if v}
        if cleaned == self.get():
            return
        self._settings.setValue(SETTINGS_KEY, json.dumps(cleaned, ensure_ascii=False))
        log.info("바디 시차: %s", ", ".join(f"{k} {format_offset(v)}" for k, v in cleaned.items()) or "없음")
        self.changed.emit()

    def as_key(self) -> tuple[tuple[str, float], ...]:
        """비교·캐시 키로 쓸 수 있는 모양(정렬된 튜플)."""
        return tuple(sorted(self.get().items()))


_store: ClockOffsetStore | None = None


def offset_store() -> ClockOffsetStore:
    global _store
    if _store is None:
        _store = ClockOffsetStore()
    return _store


def describe_offsets(offsets: dict[str, float]) -> tuple[str, str]:
    """화면에 붙일 짧은 상태 글자와 툴팁."""
    if not offsets:
        return "보정 없음", "바디별 시계 보정이 없습니다."
    lines = [f"{key}: {format_offset(seconds)}" for key, seconds in sorted(offsets.items())]
    return f"{len(offsets)}대 보정", "<br>".join(lines)


class OffsetSpinBox(QSpinBox):
    """시차(초)를 "+1:23" 모양으로 보여주고 입력받는 칸."""

    def __init__(self) -> None:
        super().__init__()
        self.setRange(-MAX_OFFSET_SECONDS, MAX_OFFSET_SECONDS)
        self.setAccelerated(True)
        self.setMinimumWidth(110)
        self.setAlignment(Qt.AlignmentFlag.AlignRight)

    def textFromValue(self, value: int) -> str:  # noqa: N802 - Qt override signature
        return format_offset(value)

    def valueFromText(self, text: str) -> int:  # noqa: N802 - Qt override signature
        try:
            return int(parse_offset(text))
        except ValueError:
            return self.value()

    def validate(self, text: str, pos: int):  # noqa: N802 - Qt override signature
        stripped = text.strip()
        if stripped in ("", "+", "-"):
            return QValidator.State.Intermediate, text, pos
        try:
            parse_offset(stripped.rstrip(":"))
        except ValueError:
            return QValidator.State.Invalid, text, pos
        state = QValidator.State.Intermediate if stripped.endswith(":") else QValidator.State.Acceptable
        return state, text, pos


class _ScanWorker(QThread):
    finished_ok = Signal(list)
    failed = Signal(str)

    def __init__(self, folders: list[Path], recursive: bool, cache: dict | None) -> None:
        super().__init__()
        self._args = (folders, recursive, cache)

    def run(self) -> None:
        try:
            folders, recursive, cache = self._args
            self.finished_ok.emit(scan_and_extract_many(folders, recursive=recursive, cache=cache,
                                                        include_unpaired_raw=True))
        except Exception as e:  # noqa: BLE001 - surfaced to the user, not swallowed
            log.exception("바디 시계: 사진 정보 읽기 실패")
            self.failed.emit(str(e))


_COLUMNS = ("바디(기종 #시리얼)", "사진", "첫 사진", "마지막 사진", "시차", "맞춘 뒤 첫 사진")


class ClockSyncDialog(QDialog):
    def __init__(self, folders: list[Path], recursive: bool, cache: dict | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("바디 시계 맞추기")
        self.resize(920, 560)
        self._store = offset_store()
        self._cameras: list[CameraInfo] = []
        self._spins: dict[str, OffsetSpinBox] = {}
        self._pair: list[PhotoMetadata | None] = [None, None]

        intro = QLabel(
            "바디마다 시계가 어긋난 만큼 시차를 넣으면, 시간순 정렬과 장면 묶기가 맞춘 시각으로 계산합니다. "
            "원본 파일의 촬영 시각은 바꾸지 않습니다. 시차는 바디(기종+시리얼)마다 기억합니다."
        )
        intro.setWordWrap(True)
        self._status = QLabel("사진 정보를 읽는 중...")
        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, len(_COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)

        # 두 사진으로 맞추기
        self._pair_labels = [QLabel("(고르지 않음)"), QLabel("(고르지 않음)")]
        self._pair_images = [QLabel(), QLabel()]
        pair_grid = QGridLayout()
        for column, title in enumerate(("① 기준 바디 사진", "② 맞출 바디 사진")):
            button = QPushButton(f"{title} 고르기…")
            button.clicked.connect(lambda _c=False, i=column: self._choose_pair(i))
            self._pair_images[column].setFixedSize(160, 110)
            self._pair_images[column].setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._pair_images[column].setStyleSheet("background: palette(base);")
            self._pair_labels[column].setWordWrap(True)
            pair_grid.addWidget(button, 0, column)
            pair_grid.addWidget(self._pair_images[column], 1, column)
            pair_grid.addWidget(self._pair_labels[column], 2, column)
        self._pair_button = QPushButton("두 사진의 시각이 같아지게 ② 바디 시차 넣기")
        self._pair_button.setEnabled(False)
        self._pair_button.clicked.connect(self._apply_pair)
        pair_grid.addWidget(self._pair_button, 3, 0, 1, 2)
        pair_box = QGroupBox("같은 순간을 찍은 두 사진으로 맞추기")
        pair_box.setLayout(pair_grid)

        reset_button = QPushButton("모두 0으로")
        reset_button.clicked.connect(lambda: [spin.setValue(0) for spin in self._spins.values()])
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("저장")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.addWidget(reset_button)
        bottom.addStretch()
        bottom.addWidget(buttons)

        layout = QVBoxLayout()
        layout.addWidget(intro)
        layout.addWidget(self._status)
        layout.addWidget(self._table, stretch=1)
        layout.addWidget(pair_box)
        layout.addLayout(bottom)
        self.setLayout(layout)

        self._worker = _ScanWorker(list(folders), recursive, cache)
        self._worker.finished_ok.connect(self._on_scanned)
        self._worker.failed.connect(lambda message: self._status.setText(f"사진 정보를 읽지 못했습니다: {message}"))
        self._worker.start()

    # --- 바디 목록 ---------------------------------------------------------------
    def offsets(self) -> dict[str, float]:
        """지금 칸에 넣은 시차(목록에 없는 바디의 저장된 시차는 그대로 둔다)."""
        offsets = dict(self._store.get())
        for key, spin in self._spins.items():
            offsets[key] = float(spin.value())
        return {k: v for k, v in offsets.items() if v}

    def _on_scanned(self, photos: list[PhotoMetadata]) -> None:
        self._cameras, unknown = summarize(photos)
        saved = self._store.get()
        self._table.setRowCount(len(self._cameras))
        for row, camera in enumerate(self._cameras):
            values = (camera.key, f"{camera.count}장", f"{camera.first:%m-%d %H:%M:%S}", f"{camera.last:%m-%d %H:%M:%S}")
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                if column == 0:
                    item.setToolTip(f"예: {camera.example}")
                self._table.setItem(row, column, item)
            spin = OffsetSpinBox()
            spin.setValue(int(saved.get(camera.key, 0)))
            spin.valueChanged.connect(self._refresh_shifted)
            self._spins[camera.key] = spin
            self._table.setCellWidget(row, 4, spin)
        self._refresh_shifted()
        if not self._cameras:
            self._status.setText("카메라 정보가 있는 사진이 없습니다.")
        else:
            note = f" · 카메라 정보 없음 {unknown}개(같은 폴더 사진의 바디를 따르거나 보정 안 함)" if unknown else ""
            self._status.setText(f"바디 {len(self._cameras)}대{note}")

    def _refresh_shifted(self) -> None:
        for row, camera in enumerate(self._cameras):
            seconds = self._spins[camera.key].value()
            first = camera.first + datetime.timedelta(seconds=seconds)
            self._table.setItem(row, 5, QTableWidgetItem(f"{first:%m-%d %H:%M:%S}"))

    # --- 두 사진으로 맞추기 ---------------------------------------------------------
    def _choose_pair(self, index: int) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "같은 순간을 찍은 사진 고르기", "", "사진 (*.jpg *.jpeg *.png *.heic *.heif *.cr2 *.cr3 *.nef *.arw *.raf *.orf *.rw2 *.dng)"
        )
        if path:
            self.set_pair_photo(index, Path(path))

    def set_pair_photo(self, index: int, path: Path) -> None:
        meta = extract_metadata(path)
        self._pair[index] = meta
        key = camera_key(meta) or "카메라 정보 없음"
        self._pair_labels[index].setText(f"{path.name}\n{key}\n{meta.datetime_original:%Y-%m-%d %H:%M:%S}")
        data = make_thumbnail_jpeg(path, (160, 110))
        pixmap = QPixmap()
        if data is not None and pixmap.loadFromData(data, "JPEG"):
            self._pair_images[index].setPixmap(pixmap)
        else:
            self._pair_images[index].setText("미리보기 없음")
        self._pair_button.setEnabled(all(m is not None for m in self._pair))

    def _apply_pair(self) -> None:
        reference, other = self._pair
        try:
            key, seconds = offset_from_pair(reference, other, self.offsets())
        except ValueError as e:
            QMessageBox.warning(self, "바디 시계 맞추기", str(e))
            return
        if key not in self._spins:
            QMessageBox.warning(
                self, "바디 시계 맞추기", f"② 사진의 바디({key})가 입력 폴더의 사진에 없습니다."
            )
            return
        self._spins[key].setValue(int(round(seconds)))
        QMessageBox.information(
            self, "바디 시계 맞추기", f"{key}에 {format_offset(seconds)}를 넣었습니다. 저장을 눌러야 적용됩니다."
        )

    def _save(self) -> None:
        self._store.set(self.offsets())
        self.accept()
