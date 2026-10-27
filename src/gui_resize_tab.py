"""사진 축소 화면: 폴더 안 사진을 긴 변 기준 픽셀로 줄여 JPG로 새 폴더에 저장한다(resize.py).

미리보기(파일 머리만 읽어 크기 계산) → 표로 확인 → "축소해서 저장"으로만 파일을 만든다.
원본은 읽기만 한다. 긴 변 픽셀을 바꾸면 사진을 다시 읽지 않고 표를 즉시 다시 계산한다.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import app_log
from gui_dnd import extract_dropped_folders
from gui_format import format_resize_plan_summary, format_resize_result
from gui_settings import load_bool, load_float, load_folder, make_settings, save_bool, save_float, save_folder
from gui_widgets import GuidePanel, PathLabel, SummaryLabel, compact, fit_text, make_primary, status_row
from gui_worker import CancellableWorker, PreviewCancelled
from resize import (
    ACTION_ERROR,
    ACTION_LABELS,
    ACTION_RESIZE,
    LONG_EDGE_MAX,
    LONG_EDGE_MIN,
    ResizePlan,
    ResizePlanRow,
    apply_resize_plan,
    build_resize_plan,
    replan,
)

INPUT_KEY = "resize/input_dir"
OUTPUT_KEY = "resize/output_dir"
RECURSIVE_KEY = "resize/recursive"
LONG_EDGE_KEY = "resize/long_edge"
QUALITY_KEY = "resize/quality"
LONG_EDGE_START = 2048  # 처음 켰을 때 칸에 넣어 두는 값(사용자가 바꾸면 기억)
QUALITY_START = 90

_COLUMNS = ("파일", "원래 크기", "새 크기", "처리", "저장 이름")

log = app_log.get("resize")


def _size_text(size: tuple[int, int] | None) -> str:
    return f"{size[0]} × {size[1]}" if size else "—"


class PlanWorker(CancellableWorker):
    progress = Signal(int, int)
    finished_ok = Signal(object)  # ResizePlan
    failed = Signal(str)

    def __init__(self, input_dir: Path, output_dir: Path, long_edge: int, recursive: bool) -> None:
        super().__init__()
        self._args = (input_dir, output_dir, long_edge, recursive)

    def run(self) -> None:
        def on_progress(done: int, total: int) -> None:
            self.check_cancel()
            self.progress.emit(done, total)

        try:
            input_dir, output_dir, long_edge, recursive = self._args
            plan = build_resize_plan(input_dir, output_dir, long_edge, recursive, on_progress)
            self.finished_ok.emit(plan)
        except PreviewCancelled:
            self.cancelled.emit()
        except Exception as e:  # noqa: BLE001 - surfaced to the user, not swallowed
            log.exception("%s 실패", type(self).__name__)
            self.failed.emit(str(e))


class SaveWorker(CancellableWorker):
    progress = Signal(int, int)
    finished_ok = Signal(list)  # list[ResizeResult]
    failed = Signal(str)

    def __init__(self, rows: list[ResizePlanRow], output_dir: Path, long_edge: int, quality: int) -> None:
        super().__init__()
        self._args = (rows, output_dir, long_edge, quality)

    def run(self) -> None:
        def on_progress(done: int, total: int) -> None:
            self.progress.emit(done, total)
            self.check_cancel()

        try:
            self.finished_ok.emit(apply_resize_plan(*self._args, on_progress=on_progress))
        except PreviewCancelled:
            self.cancelled.emit()
        except Exception as e:  # noqa: BLE001 - surfaced to the user, not swallowed
            log.exception("%s 실패", type(self).__name__)
            self.failed.emit(str(e))


class ResizeTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._settings = make_settings()
        self._input_dir: Path | None = load_folder(self._settings, INPUT_KEY)
        self._output_dir: Path | None = load_folder(self._settings, OUTPUT_KEY)
        self._plan: ResizePlan | None = None
        self._plan_long_edge: int | None = None
        self._plan_worker: PlanWorker | None = None
        self._save_worker: SaveWorker | None = None
        self._busy = False
        self._saving = False
        # 저장한 뒤에는 저장 폴더에 파일이 생겨 다음 저장 이름이 달라지므로, 표는 남기되 다시
        # 미리보기해야 저장할 수 있다.
        self._saved = False
        self.setAcceptDrops(True)

        self._input_label = PathLabel("(폴더를 고르거나 끌어다 놓으세요)")
        self._input_label.set_path(self._input_dir)
        input_button = compact(QPushButton("선택…"))
        input_button.clicked.connect(self._choose_input_dir)
        self._recursive_checkbox = QCheckBox("하위 폴더까지 포함 (같은 폴더 구조로 저장)")
        self._recursive_checkbox.setChecked(load_bool(self._settings, RECURSIVE_KEY, False))
        self._recursive_checkbox.toggled.connect(self._on_recursive_changed)
        self._output_label = PathLabel()
        self._output_label.set_path(self._output_dir)
        output_button = compact(QPushButton("선택…"))
        output_button.clicked.connect(self._choose_output_dir)
        self._open_output_button = compact(QPushButton("열기"))
        self._open_output_button.clicked.connect(self._open_output_dir)
        self._open_output_button.setEnabled(self._output_dir is not None)

        self._long_edge_spin = QSpinBox()
        self._long_edge_spin.setRange(LONG_EDGE_MIN, LONG_EDGE_MAX)
        self._long_edge_spin.setSingleStep(100)
        self._long_edge_spin.setSuffix(" px")
        self._long_edge_spin.setValue(
            int(load_float(self._settings, LONG_EDGE_KEY, LONG_EDGE_START, LONG_EDGE_MIN, LONG_EDGE_MAX))
        )
        self._long_edge_spin.setToolTip(
            "<p>사진의 긴 변(가로 사진은 가로, 세로 사진은 세로)을 이 픽셀로 줄이고 짧은 변은 "
            "비율대로 맞춥니다. 이미 이보다 작은 사진은 키우지 않습니다.</p>"
        )
        self._long_edge_spin.valueChanged.connect(self._on_long_edge_changed)
        self._quality_spin = QSpinBox()
        self._quality_spin.setRange(1, 100)
        self._quality_spin.setValue(int(load_float(self._settings, QUALITY_KEY, QUALITY_START, 1, 100)))
        self._quality_spin.setToolTip(
            "<p>JPG 화질(1~100). 높을수록 깨끗하지만 파일이 커집니다. 보통 85~95를 씁니다.</p>"
        )
        self._quality_spin.valueChanged.connect(
            lambda value: save_float(self._settings, QUALITY_KEY, value)
        )

        self._preview_button = make_primary(QPushButton("미리보기"))
        self._preview_button.setToolTip("<p>어떤 사진을 몇 픽셀로 줄일지 보여줍니다. 파일은 아직 만들지 않습니다.</p>")
        self._preview_button.clicked.connect(self._run_preview)
        self._cancel_button = compact(QPushButton("취소"))
        self._cancel_button.setEnabled(False)
        self._cancel_button.clicked.connect(self._cancel)
        self._save_button = make_primary(QPushButton("축소해서 저장"))
        self._save_button.clicked.connect(self._run_save)
        self._progress_bar = QProgressBar()
        self._status_label = QLabel("")
        self._summary = SummaryLabel()

        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, len(_COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self._guide = GuidePanel(
            "사진 축소 순서",
            [
                "줄일 사진 폴더 고르기 (끌어다 놓아도 됩니다)",
                "축소본을 저장할 폴더 고르기 (원본 폴더와 겹치지 않는 곳)",
                "긴 변 픽셀과 JPG 화질 정하기",
                "미리보기 — 사진마다 몇 픽셀로 줄일지 확인 (파일은 아직 그대로)",
                "축소해서 저장",
            ],
        )
        self._results = QStackedWidget()
        self._results.addWidget(self._guide)
        self._results.addWidget(self._table)

        input_row = QHBoxLayout()
        input_row.addWidget(self._input_label, stretch=1)
        input_row.addWidget(input_button)
        output_row = QHBoxLayout()
        output_row.addWidget(self._output_label, stretch=1)
        output_row.addWidget(output_button)
        output_row.addWidget(self._open_output_button)
        folder_form = QFormLayout()
        folder_form.addRow("사진 폴더:", input_row)
        folder_form.addRow("", self._recursive_checkbox)
        folder_form.addRow("저장 폴더:", output_row)
        folder_group = QGroupBox("폴더")
        folder_group.setLayout(folder_form)

        size_form = QFormLayout()
        size_form.addRow("긴 변:", self._long_edge_spin)
        size_form.addRow("JPG 화질:", self._quality_spin)
        note = QLabel("JPG로 저장 · 원본 그대로 · 촬영 정보(EXIF) 유지 · RAW는 건너뜀")
        note.setWordWrap(True)
        note.setEnabled(False)
        size_form.addRow(note)
        size_group = QGroupBox("크기·화질")
        size_group.setLayout(size_form)

        top_row = QHBoxLayout()
        top_row.addWidget(folder_group, stretch=3)
        top_row.addWidget(size_group, stretch=2)

        button_row = QHBoxLayout()
        button_row.addWidget(self._preview_button)
        button_row.addWidget(self._cancel_button)
        button_row.addSpacing(12)
        button_row.addLayout(status_row(self._progress_bar, self._status_label), stretch=1)
        button_row.addWidget(self._save_button)

        layout = QVBoxLayout()
        layout.addLayout(top_row)
        layout.addLayout(button_row)
        layout.addWidget(self._summary)
        layout.addWidget(self._results, stretch=1)
        self.setLayout(layout)
        self._refresh()

    @property
    def is_busy(self) -> bool:
        return self._busy

    # --- 폴더 ---------------------------------------------------------------------
    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt override signature
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt override signature
        folders = extract_dropped_folders(event.mimeData())
        if folders:
            self._set_input_dir(folders[0])
        event.acceptProposedAction()

    def _choose_input_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "줄일 사진 폴더 선택")
        if directory:
            self._set_input_dir(Path(directory))

    def _set_input_dir(self, folder: Path) -> None:
        self._input_dir = folder
        self._input_label.set_path(folder)
        save_folder(self._settings, INPUT_KEY, folder)
        self._invalidate()

    def _choose_output_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "축소본을 저장할 폴더 선택")
        if directory:
            self._output_dir = Path(directory)
            self._output_label.set_path(self._output_dir)
            self._open_output_button.setEnabled(True)
            save_folder(self._settings, OUTPUT_KEY, self._output_dir)
            # 저장 이름(겹치면 " (1)")이 출력 폴더에 따라 달라 다시 미리보기
            self._invalidate()

    def _open_output_dir(self) -> None:
        if self._output_dir is not None and not QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(self._output_dir))
        ):
            QMessageBox.warning(self, "폴더 열기 실패", f"폴더를 열지 못했습니다: {self._output_dir}")

    def _on_recursive_changed(self, checked: bool) -> None:
        save_bool(self._settings, RECURSIVE_KEY, checked)
        self._invalidate()

    def _on_long_edge_changed(self, value: int) -> None:
        save_float(self._settings, LONG_EDGE_KEY, value)
        if self._plan is not None:
            # 원래 크기는 이미 알고 있어 사진을 다시 읽지 않고 즉시 다시 계산
            self._plan = ResizePlan(replan(self._plan.rows, value), self._plan.skipped_raw)
            self._plan_long_edge = value
            self._populate()
        self._refresh()

    def _invalidate(self) -> None:
        self._plan = None
        self._summary.setPlainText("")
        self._table.setRowCount(0)
        self._refresh()

    # --- 미리보기 -----------------------------------------------------------------
    def _run_preview(self) -> None:
        if self._busy:
            return
        if self._input_dir is None or self._output_dir is None:
            QMessageBox.warning(self, "폴더 필요", "사진 폴더와 저장 폴더를 먼저 고르세요.")
            return
        self._set_busy(True)
        self._status_label.setText("사진 크기 읽는 중...")
        long_edge = self._long_edge_spin.value()
        self._plan_worker = PlanWorker(
            self._input_dir, self._output_dir, long_edge, self._recursive_checkbox.isChecked()
        )
        self._plan_worker.progress.connect(self._on_progress)
        self._plan_worker.finished_ok.connect(self._on_plan_done)
        self._plan_worker.failed.connect(self._on_error)
        self._plan_worker.cancelled.connect(self._on_cancelled)
        self._plan_worker.start()

    def _on_plan_done(self, plan: ResizePlan) -> None:
        self._set_busy(False)
        long_edge = self._long_edge_spin.value()
        # 미리보기 도중 긴 변을 바꿨으면 지금 값으로 맞춘다
        self._plan = ResizePlan(replan(plan.rows, long_edge), plan.skipped_raw)
        self._plan_long_edge = long_edge
        self._saved = False
        self._status_label.setText("완료")
        self._populate()
        self._refresh()

    def _populate(self) -> None:
        plan = self._plan
        if plan is None:
            return
        self._summary.setPlainText(
            format_resize_plan_summary(plan.rows, plan.skipped_raw, self._plan_long_edge or 0)
        )
        base = self._input_dir
        self._table.setRowCount(len(plan.rows))
        for i, row in enumerate(plan.rows):
            try:
                shown = str(row.source.relative_to(base)) if base else row.source.name
            except ValueError:
                shown = row.source.name
            action = ACTION_LABELS[row.action]
            values = (
                shown,
                _size_text(row.original_size),
                _size_text(row.new_size),
                action,
                row.destination.name if row.action != ACTION_ERROR else "—",
            )
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                if column == 0:
                    item.setToolTip(str(row.source))
                if column == 3 and row.error:
                    item.setToolTip(row.error)
                if row.action != ACTION_RESIZE:
                    item.setForeground(self.palette().placeholderText())
                if column in (1, 2):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self._table.setItem(i, column, item)

    # --- 저장 ---------------------------------------------------------------------
    def _saveable_rows(self) -> list[ResizePlanRow]:
        if self._plan is None or self._saved:
            return []
        return [r for r in self._plan.rows if r.action != ACTION_ERROR]

    def _run_save(self) -> None:
        rows = self._saveable_rows()
        if self._busy or not rows or self._output_dir is None:
            return
        long_edge = self._long_edge_spin.value()
        quality = self._quality_spin.value()
        confirm = QMessageBox.question(
            self,
            "축소해서 저장",
            f"{len(rows)}장을 긴 변 {long_edge}px, JPG 화질 {quality}로 다음 폴더에 저장합니다:\n"
            f"{self._output_dir}\n\n원본은 그대로 두고, 같은 이름의 파일이 있으면 덮어쓰지 않고 "
            "번호를 붙입니다. 계속하시겠습니까?",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self._saving = True
        self._set_busy(True)
        self._status_label.setText("저장 중...")
        self._save_worker = SaveWorker(rows, self._output_dir, long_edge, quality)
        self._save_worker.progress.connect(self._on_progress)
        self._save_worker.finished_ok.connect(self._on_save_done)
        self._save_worker.failed.connect(self._on_error)
        self._save_worker.cancelled.connect(self._on_cancelled)
        self._save_worker.start()

    def _on_save_done(self, results: list) -> None:
        self._saving = False
        self._saved = True
        self._set_busy(False)
        source_bytes = saved_bytes = 0
        for result in results:
            if result.error is None and result.destination is not None:
                try:
                    source_bytes += result.source.stat().st_size
                    saved_bytes += result.destination.stat().st_size
                except OSError:
                    pass
        self._summary.setPlainText(
            format_resize_result(results, self._output_dir, source_bytes, saved_bytes)
        )
        failed = sum(1 for r in results if r.error is not None)
        self._status_label.setText(f"저장 완료 ({len(results) - failed}장) · 다시 저장하려면 미리보기부터")
        self._refresh()

    # --- 공통 ---------------------------------------------------------------------
    def _cancel(self) -> None:
        for worker in (self._plan_worker, self._save_worker):
            if worker is not None and worker.isRunning():
                worker.request_cancel()
        self._cancel_button.setEnabled(False)
        self._status_label.setText("멈추는 중...")

    def _on_cancelled(self) -> None:
        saving = self._saving
        self._saving = False
        self._set_busy(False)
        if saving:
            # 이미 만든 축소본은 그대로 남는다(원본과 무관). 이어서 하려면 다시 미리보기
            self._saved = True
            self._summary.setPlainText(
                "저장을 멈췄습니다. 그때까지 만든 축소본은 저장 폴더에 남아 있습니다(처리 기록: "
                "resize_log_*.jsonl)."
            )
        self._status_label.setText("취소했습니다")
        self._refresh()

    def _on_progress(self, done: int, total: int) -> None:
        self._progress_bar.setMaximum(total)
        self._progress_bar.setValue(done)

    def _on_error(self, message: str) -> None:
        if self._saving:
            self._saving = False
            self._saved = True  # 일부는 이미 저장됐을 수 있어 다시 미리보기부터
        self._set_busy(False)
        self._status_label.setText("")
        QMessageBox.critical(self, "오류", message)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._cancel_button.setEnabled(busy)
        if busy:
            self._progress_bar.setValue(0)
        self._refresh()

    def _refresh(self) -> None:
        count = len(self._saveable_rows())
        self._preview_button.setEnabled(not self._busy)
        self._save_button.setText(f"축소해서 저장 ({count}장)" if count else "축소해서 저장")
        fit_text(self._save_button)
        self._save_button.setEnabled(bool(count) and not self._busy and self._output_dir is not None)
        has_plan = self._plan is not None
        self._results.setCurrentWidget(self._table if has_plan else self._guide)
        self._guide.set_done(
            [
                self._input_dir is not None,
                self._output_dir is not None,
                has_plan,  # 크기·화질은 기억된 값이 있어, 미리보기를 하면 정한 것으로 본다
                has_plan,
                self._saved,
            ]
        )
