"""M10: 흔들림/눈감음 임계값 미달 사진(C컷)을 판정해 이동하는 탭.

스코어링(사진마다 ~100ms대, 장 수가 많으면 분 단위)과 임계값 재분류(즉시 끝남)를
분리해뒀다 — 미리보기 버튼으로 한 번 스코어링해두면, 그 뒤로 임계값(민감도)
스핀박스를 바꿀 때마다 사진을 다시 읽지 않고 즉시 재분류해 결과가 갱신된다.

선명도(라플라시안 분산)는 피사체에 따라 범위가 크게 달라 숫자만으로는
감이 오지 않으므로, 표에서(또는 크게 보기 창에서) 사진을 골라 "이 사진까지는
정상" 기준 사진으로 지정하면 그 사진의 점수가 임계값이 되게 했다. 눈뜸도 같은
방식이다. 숫자를 직접 넣는 스핀박스는 미세 조정용으로 그대로 둔다.
"""

from __future__ import annotations

from pathlib import Path

from PIL.ImageQt import ImageQt
from PySide6.QtCore import QSize, Qt, QThread, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from badcut import BadCutThresholds, classify_badcut, score_for_badcut
from file_ops import FileOpResult, apply_selection
from gui_dnd import extract_dropped_folders
from gui_format import (
    format_apply_summary,
    format_badcut_header,
    format_sharpness_region,
    format_threshold_reference,
    sharpness_measure_help,
)
import score_store
from gui_worker import CancellableWorker, PreviewCancelled
from gui_photo_viewer import PhotoViewerDialog, ViewerAction
from gui_settings import load_folder, load_folder_list, make_settings, save_folder, save_folder_list
from report import ReportRow, write_apply_report
from scanner import PhotoMetadata, scan_and_extract_many
from scoring import PhotoScore
from thumbnails import make_thumbnail


_SHARPNESS_TOOLTIP = (
    "<p><b>선명도</b>: 사진 속 경계선(디테일)이 얼마나 또렷한지를 나타내는 값입니다. "
    "흔들리거나 초점이 나가면 낮아지고, 또렷할수록 높아집니다.</p>"
    + sharpness_measure_help()
    + "<p>그래서 절대적인 숫자보다는 <b>같은 폴더 사진끼리 비교</b>하는 용도입니다.</p>"
    "<p>쉽게 정하는 법: 표의 '선명도' 머리글을 눌러 정렬하고, 사진을 더블클릭해 크게 "
    "보면서 '이 정도까지는 쓸 만하다' 싶은 사진을 고른 뒤 "
    "<b>[선택한 사진을 선명도 기준으로]</b>를 누르세요. 그 사진보다 선명도가 낮은 "
    "사진이 C컷(블러)이 되고, 기준 사진 자체는 정상으로 남습니다.</p>"
)
_EYES_TOOLTIP = (
    "<p><b>눈뜸</b>: 얼굴이 검출된 사진에서 눈을 뜬 정도입니다. "
    "0이면 완전히 감음, 1이면 완전히 뜬 상태입니다.</p>"
    "<p>한쪽 눈만 감아도 더 감긴 쪽 눈을 기준으로 보고, 여러 명이 찍혔으면 "
    "얼굴별 값의 평균을 씁니다. 얼굴이 검출되지 않은 사진(측면·뒷모습·풍경 등)은 "
    "눈뜸 판정에서 빠집니다(표에 빈칸으로 표시).</p>"
    "<p>쉽게 정하는 법: '눈뜸'으로 정렬해 크게 보면서 '이 정도 감은 건 괜찮다' 싶은 "
    "사진을 고른 뒤 <b>[선택한 사진을 눈뜸 기준으로]</b>를 누르세요. 그보다 눈이 더 "
    "감긴 사진이 C컷(눈감음)이 됩니다.</p>"
)
_MANUAL_SPIN_TOOLTIP = (
    "<p>숫자를 직접 넣어 미세 조정할 수도 있습니다. 직접 바꾸면 기준 사진 지정은 "
    "풀리고 입력한 값이 그대로 임계값이 됩니다.</p>"
    "<p>한 번 미리보기(스코어링)한 뒤에는 값을 바꿀 때마다 다시 스캔하지 않고 즉시 "
    "재판정합니다.</p>"
)

# 표에 넣는 행 → ReportRow를 찾기 위해 파일명 칸에 경로를 함께 넣어둔다.
# (정렬하면 표의 행 순서가 self._rows 순서와 달라지기 때문)
_PATH_ROLE = Qt.ItemDataRole.UserRole


def _percent(done: int, total: int) -> int:
    return int(done / total * 100) if total else 0


class ScoreWorker(CancellableWorker):
    progress = Signal(int, int)
    status = Signal(str)
    finished_ok = Signal(list, list)  # list[PhotoMetadata], list[PhotoScore]
    failed = Signal(str)

    def __init__(
        self,
        input_dirs: list[Path],
        recursive: bool,
        scan_cache: dict | None = None,
        score_cache: dict | None = None,
        thumbnail_cache: dict | None = None,
    ) -> None:
        super().__init__()
        self._input_dirs = input_dirs
        self._recursive = recursive
        self._scan_cache = scan_cache
        self._score_cache = score_cache
        self._thumbnail_cache = thumbnail_cache

    def run(self) -> None:
        try:
            def on_scan_progress(done: int, total: int, path: Path) -> None:
                self.check_cancel()
                self.progress.emit(done, total)
                self.status.emit(f"스캔 중: {path.name} ({done}/{total}, {_percent(done, total)}%)")

            photos = scan_and_extract_many(
                self._input_dirs,
                recursive=self._recursive,
                on_progress=on_scan_progress,
                cache=self._scan_cache,
            )

            def on_score_progress(done: int, total: int) -> None:
                self.check_cancel()
                self.progress.emit(done, total)
                self.status.emit(f"스코어링 중... ({done}/{total}, {_percent(done, total)}%)")

            scores = score_for_badcut(photos, on_progress=on_score_progress, cache=self._score_cache)
            self._generate_thumbnails(photos)
            self.finished_ok.emit(photos, scores)
        except PreviewCancelled:
            self.cancelled.emit()
        except Exception as e:  # noqa: BLE001 - surfaced to the user, not swallowed
            self.failed.emit(str(e))

    def _generate_thumbnails(self, photos: list[PhotoMetadata]) -> None:
        if self._thumbnail_cache is None:
            return
        for meta in photos:
            self.check_cancel()
            try:
                stat = meta.path.stat()
            except OSError:
                continue
            key = (stat.st_mtime, stat.st_size)
            cached = self._thumbnail_cache.get(meta.path)
            if cached is not None and cached[:2] == key:
                continue
            self._thumbnail_cache[meta.path] = (*key, make_thumbnail(meta.path))


class ApplyWorker(QThread):
    progress = Signal(int, int)
    finished_ok = Signal(list, object)  # list[FileOpResult], 판정 리포트 경로
    failed = Signal(str)

    def __init__(self, rows: list[ReportRow], output_dir: Path) -> None:
        super().__init__()
        self._rows = rows
        self._output_dir = output_dir

    def run(self) -> None:
        try:
            results = apply_selection(
                self._rows, self._output_dir, move=True, on_progress=self.progress.emit
            )
            # 파일 작업이 끝난 뒤에 쓴다 — 출력 폴더가 원본 폴더와 겹쳐 apply_selection이
            # 막은 경우엔 원본 폴더에 리포트가 생기지 않도록.
            report_path = write_apply_report(self._rows, self._output_dir, "badcut")
            self.finished_ok.emit(results, report_path)
        except Exception as e:  # noqa: BLE001 - surfaced to the user, not swallowed
            self.failed.emit(str(e))


class BadCutTab(QWidget):
    def __init__(self) -> None:
        super().__init__()

        self._input_dirs: list[Path] = []
        self._output_dir: Path | None = None
        self._photos: list[PhotoMetadata] | None = None
        self._scores: list[PhotoScore] | None = None
        self._rows: list[ReportRow] | None = None
        self._score_worker: ScoreWorker | None = None
        self._apply_worker: ApplyWorker | None = None
        self._busy = False
        # 실제 판정에 쓰는 임계값. 스핀박스는 소수 1~2자리로 반올림해 보여주기만
        # 하므로(예: 기준 사진 눈뜸 0.456이 0.46으로 반올림되면 기준 사진 자신이
        # C컷이 돼버림), 기준 사진을 고른 경우엔 그 사진의 정확한 점수를 여기 둔다.
        self._sharpness_threshold = 0.0
        self._eyes_threshold = 0.0
        self._sharpness_ref_name: str | None = None
        self._eyes_ref_name: str | None = None
        self._viewer: PhotoViewerDialog | None = None
        self._viewer_paths: list[Path] = []
        # 같은 세션(앱 종료 전까지) 동안 이미 스캔/스코어링한 파일은 다시 처리하지
        # 않도록 재사용하는 인메모리 캐시. (mtime, size)가 바뀐 파일만 다시 계산됨.
        self._scan_cache: dict = {}
        # 점수는 앱을 꺼도 남는 공용 저장소를 쓴다(베스트컷 탭과 공유, score_store).
        self._score_cache: dict = score_store.score_cache()
        self._thumbnail_cache: dict = {}
        self._pixmap_cache: dict = {}
        self.setAcceptDrops(True)
        self._settings = make_settings()

        self._folder_list = QListWidget()
        add_input_button = QPushButton("입력 폴더 추가...")
        add_input_button.clicked.connect(self._add_input_dir)
        remove_input_button = QPushButton("선택 삭제")
        remove_input_button.clicked.connect(self._remove_selected_input_dir)

        self._output_label = QLabel("출력 폴더: (선택 안 됨)")
        output_button = QPushButton("출력 폴더 선택...")
        output_button.clicked.connect(self._choose_output_dir)

        self._recursive_checkbox = QCheckBox("하위 폴더까지 포함")

        self._min_sharpness_spin = QDoubleSpinBox()
        self._min_sharpness_spin.setRange(0.0, 1_000_000.0)
        self._min_sharpness_spin.setDecimals(1)
        self._min_sharpness_spin.setSingleStep(10.0)
        self._min_sharpness_spin.setToolTip(_SHARPNESS_TOOLTIP + _MANUAL_SPIN_TOOLTIP)
        self._min_sharpness_spin.valueChanged.connect(self._on_sharpness_spin_changed)

        self._min_eyes_open_spin = QDoubleSpinBox()
        self._min_eyes_open_spin.setRange(0.0, 1.0)
        self._min_eyes_open_spin.setDecimals(2)
        self._min_eyes_open_spin.setSingleStep(0.05)
        self._min_eyes_open_spin.setToolTip(_EYES_TOOLTIP + _MANUAL_SPIN_TOOLTIP)
        self._min_eyes_open_spin.valueChanged.connect(self._on_eyes_spin_changed)

        self._sharpness_ref_label = QLabel()
        self._sharpness_ref_label.setToolTip(_SHARPNESS_TOOLTIP)
        self._eyes_ref_label = QLabel()
        self._eyes_ref_label.setToolTip(_EYES_TOOLTIP)
        self._update_reference_labels()

        sharpness_label = QLabel("선명도 임계값 (블러) ⓘ:")
        sharpness_label.setToolTip(_SHARPNESS_TOOLTIP)
        eyes_label = QLabel("눈뜸 임계값 (눈감음) ⓘ:")
        eyes_label.setToolTip(_EYES_TOOLTIP)

        sharpness_row = QHBoxLayout()
        sharpness_row.addWidget(self._min_sharpness_spin)
        sharpness_row.addWidget(self._sharpness_ref_label, stretch=1)
        eyes_row = QHBoxLayout()
        eyes_row.addWidget(self._min_eyes_open_spin)
        eyes_row.addWidget(self._eyes_ref_label, stretch=1)

        threshold_form = QFormLayout()
        threshold_form.addRow(sharpness_label, sharpness_row)
        threshold_form.addRow(eyes_label, eyes_row)

        self._sharpness_ref_button = QPushButton("선택한 사진을 선명도 기준으로")
        self._sharpness_ref_button.setToolTip(
            "<p>표에서 고른 사진의 선명도까지는 정상으로 보고, 이보다 선명도가 낮은 "
            "사진을 C컷(블러)으로 판정합니다.</p>"
        )
        self._sharpness_ref_button.clicked.connect(self._use_selected_as_sharpness_reference)
        self._eyes_ref_button = QPushButton("선택한 사진을 눈뜸 기준으로")
        self._eyes_ref_button.setToolTip(
            "<p>표에서 고른 사진의 눈 뜬 정도까지는 정상으로 보고, 이보다 눈이 더 "
            "감긴 사진을 C컷(눈감음)으로 판정합니다. 얼굴이 검출된 사진만 고를 수 "
            "있습니다.</p>"
        )
        self._eyes_ref_button.clicked.connect(self._use_selected_as_eyes_reference)
        self._view_button = QPushButton("크게 보기")
        self._view_button.setToolTip("<p>선택한 사진을 크게 엽니다. 표에서 사진을 더블클릭해도 됩니다.</p>")
        self._view_button.clicked.connect(self._open_viewer_for_selected)

        reference_button_row = QHBoxLayout()
        reference_button_row.addWidget(self._view_button)
        reference_button_row.addWidget(self._sharpness_ref_button)
        reference_button_row.addWidget(self._eyes_ref_button)

        self._cancel_preview_button = QPushButton("취소")
        self._cancel_preview_button.setEnabled(False)
        self._cancel_preview_button.setToolTip(
            "<p>진행 중인 미리보기(스캔·채점)를 멈춥니다. 그때까지 계산한 점수는 저장돼, "
            "다시 미리보기하면 이어서 계산합니다.</p>"
        )
        self._cancel_preview_button.clicked.connect(self._cancel_preview)
        self._preview_button = QPushButton("미리보기")
        self._preview_button.clicked.connect(self._run_preview)

        self._apply_button = QPushButton("적용 (이동)")
        self._apply_button.setEnabled(False)
        self._apply_button.clicked.connect(self._run_apply)

        self._progress_bar = QProgressBar()
        self._status_label = QLabel("")
        self._summary_text = QPlainTextEdit()
        self._summary_text.setReadOnly(True)
        self._summary_text.setAcceptDrops(False)

        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(["썸네일", "파일명", "판정", "선명도", "눈뜸", "사유"])
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setIconSize(QSize(64, 64))
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setSortingEnabled(True)
        self._table.sortByColumn(3, Qt.SortOrder.AscendingOrder)
        self._table.horizontalHeaderItem(3).setToolTip(_SHARPNESS_TOOLTIP)
        self._table.horizontalHeaderItem(4).setToolTip(_EYES_TOOLTIP)
        self._table.itemSelectionChanged.connect(self._update_reference_buttons)
        self._table.cellDoubleClicked.connect(self._open_viewer)
        self._update_reference_buttons()

        input_button_row = QHBoxLayout()
        input_button_row.addWidget(add_input_button)
        input_button_row.addWidget(remove_input_button)

        button_row = QHBoxLayout()
        button_row.addWidget(self._preview_button)
        button_row.addWidget(self._cancel_preview_button)
        button_row.addWidget(self._apply_button)

        layout = QVBoxLayout()
        layout.addWidget(QLabel("입력 폴더 (여러 개 추가 가능):"))
        layout.addWidget(self._folder_list)
        layout.addLayout(input_button_row)
        layout.addWidget(self._output_label)
        layout.addWidget(output_button)
        layout.addWidget(self._recursive_checkbox)
        layout.addLayout(threshold_form)
        layout.addLayout(button_row)
        layout.addWidget(self._progress_bar)
        layout.addWidget(self._status_label)
        layout.addWidget(self._summary_text)
        layout.addWidget(
            QLabel("표 머리글을 누르면 정렬, 사진을 더블클릭하면 크게 볼 수 있습니다.")
        )
        layout.addLayout(reference_button_row)
        layout.addWidget(self._table)

        for folder in load_folder_list(self._settings, "badcut/input_dirs"):
            self._input_dirs.append(folder)
            self._folder_list.addItem(str(folder))
        restored_output = load_folder(self._settings, "badcut/output_dir")
        if restored_output is not None:
            self._output_dir = restored_output
            self._output_label.setText(f"출력 폴더: {restored_output}")

        self.setLayout(layout)

    @property
    def is_busy(self) -> bool:
        return self._busy

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt override signature
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt override signature
        folders = extract_dropped_folders(event.mimeData())
        if folders:
            self._add_folders(folders)
        event.acceptProposedAction()

    def _add_input_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "입력 폴더 추가")
        if directory:
            self._add_folders([Path(directory)])

    def _add_folders(self, folders: list[Path]) -> None:
        added = False
        for folder in folders:
            if folder not in self._input_dirs:
                self._input_dirs.append(folder)
                self._folder_list.addItem(str(folder))
                added = True
        if not added:
            return
        self._invalidate_preview()
        save_folder_list(self._settings, "badcut/input_dirs", self._input_dirs)

    def _remove_selected_input_dir(self) -> None:
        for item in self._folder_list.selectedItems():
            index = self._folder_list.row(item)
            self._folder_list.takeItem(index)
            del self._input_dirs[index]
        self._invalidate_preview()
        save_folder_list(self._settings, "badcut/input_dirs", self._input_dirs)

    def _choose_output_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "C컷을 옮길 출력 폴더 선택")
        if directory:
            self._output_dir = Path(directory)
            self._output_label.setText(f"출력 폴더: {directory}")
            # 미리보기 결과(스캔/스코어링)는 출력 폴더와 무관하므로 다시 계산할
            # 필요 없음 — 적용 버튼 활성화 여부만 갱신한다.
            bad_count = sum(1 for r in (self._rows or []) if r.selected)
            self._apply_button.setEnabled(bad_count > 0)
            save_folder(self._settings, "badcut/output_dir", self._output_dir)

    def _invalidate_preview(self) -> None:
        self._photos = None
        self._scores = None
        self._rows = None
        self._apply_button.setEnabled(False)
        self._table.setRowCount(0)
        self._update_reference_buttons()

    def _run_preview(self) -> None:
        if self._busy:
            QMessageBox.warning(
                self, "작업 진행 중", "다른 작업이 진행 중입니다. 완료될 때까지 기다려주세요."
            )
            return

        if not self._input_dirs:
            QMessageBox.warning(self, "입력 폴더 없음", "먼저 입력 폴더를 하나 이상 추가하세요.")
            return

        self._busy = True
        self._preview_button.setEnabled(False)
        self._progress_bar.setValue(0)
        self._status_label.setText("스캔/스코어링 중...")
        self._summary_text.setPlainText("스캔/스코어링 중... (한 번만 하면 이후 임계값 조정은 즉시 반영됩니다)")

        self._score_worker = ScoreWorker(
            list(self._input_dirs),
            self._recursive_checkbox.isChecked(),
            scan_cache=self._scan_cache,
            score_cache=self._score_cache,
            thumbnail_cache=self._thumbnail_cache,
        )
        self._score_worker.progress.connect(self._on_progress)
        self._score_worker.status.connect(self._status_label.setText)
        self._score_worker.finished_ok.connect(self._on_scored)
        self._score_worker.failed.connect(self._on_error)
        self._score_worker.cancelled.connect(self._on_preview_cancelled)
        self._cancel_preview_button.setEnabled(True)
        self._score_worker.start()

    def _on_scored(self, photos: list[PhotoMetadata], scores: list[PhotoScore]) -> None:
        self._busy = False
        self._cancel_preview_button.setEnabled(False)
        self._status_label.setText("완료")
        score_store.save()
        self._photos = photos
        self._scores = scores
        self._preview_button.setEnabled(True)
        self._reclassify()

    def _on_threshold_changed(self) -> None:
        # 아직 스코어링 전이면(입력 폴더를 막 바꿨거나 미리보기 전) 할 일이 없다 —
        # 미리보기를 눌러야 점수가 생기고, 그 뒤부터 이 핸들러가 즉시 재판정한다.
        self._update_reference_labels()
        if self._scores is not None:
            self._reclassify()

    def _on_sharpness_spin_changed(self, value: float) -> None:
        self._sharpness_threshold = value
        self._sharpness_ref_name = None
        self._on_threshold_changed()

    def _on_eyes_spin_changed(self, value: float) -> None:
        self._eyes_threshold = value
        self._eyes_ref_name = None
        self._on_threshold_changed()

    def _set_sharpness_reference(self, row: ReportRow) -> None:
        self._sharpness_threshold = row.sharpness
        self._sharpness_ref_name = row.path.name
        # 스핀박스는 보여주기만 갱신한다 — valueChanged가 불리면 기준 사진 지정이
        # 풀리고 반올림된 값이 임계값이 돼버린다.
        self._min_sharpness_spin.blockSignals(True)
        self._min_sharpness_spin.setValue(row.sharpness)
        self._min_sharpness_spin.blockSignals(False)
        self._on_threshold_changed()

    def _set_eyes_reference(self, row: ReportRow) -> None:
        if row.face is None:
            return
        self._eyes_threshold = row.face
        self._eyes_ref_name = row.path.name
        self._min_eyes_open_spin.blockSignals(True)
        self._min_eyes_open_spin.setValue(row.face)
        self._min_eyes_open_spin.blockSignals(False)
        self._on_threshold_changed()

    def _update_reference_labels(self) -> None:
        self._sharpness_ref_label.setText(
            format_threshold_reference(self._sharpness_ref_name, self._sharpness_threshold, 1)
        )
        self._eyes_ref_label.setText(
            format_threshold_reference(self._eyes_ref_name, self._eyes_threshold, 2)
        )

    def _selected_row(self) -> ReportRow | None:
        selected = self._table.selectionModel().selectedRows()
        if not selected or not self._rows:
            return None
        path = self._table.item(selected[0].row(), 1).data(_PATH_ROLE)
        return next((r for r in self._rows if r.path == path), None)

    def _update_reference_buttons(self) -> None:
        row = self._selected_row()
        scored = row is not None and not row.reason.startswith("판정 불가")
        self._view_button.setEnabled(row is not None)
        self._sharpness_ref_button.setEnabled(scored)
        self._eyes_ref_button.setEnabled(scored and row.face is not None)

    def _use_selected_as_sharpness_reference(self) -> None:
        row = self._selected_row()
        if row is not None:
            self._set_sharpness_reference(row)

    def _use_selected_as_eyes_reference(self) -> None:
        row = self._selected_row()
        if row is not None:
            self._set_eyes_reference(row)

    def _open_viewer_for_selected(self) -> None:
        selected = self._table.selectionModel().selectedRows()
        if selected:
            self._open_viewer(selected[0].row())

    def _open_viewer(self, table_row: int, _column: int = 0) -> None:
        if not self._rows:
            return
        # 크게 보기 창에서 ←/→로 넘기는 순서는 지금 표에 보이는 정렬 순서를 따른다.
        self._viewer_paths = [
            self._table.item(i, 1).data(_PATH_ROLE) for i in range(self._table.rowCount())
        ]
        if self._viewer is not None:
            self._viewer.close()
        self._viewer = PhotoViewerDialog(
            self._rows_in_viewer_order(),
            table_row,
            actions=[
                ViewerAction(
                    label=lambda row: "이 사진을 선명도 기준으로",
                    tooltip=(
                        "<p>이 사진의 선명도까지는 정상으로 보고, 이보다 선명도가 낮은 "
                        "사진을 C컷(블러)으로 판정합니다.</p>"
                    ),
                    enabled=lambda row: not _unscored(row),
                    triggered=self._set_sharpness_reference,
                ),
                ViewerAction(
                    label=lambda row: "이 사진을 눈뜸 기준으로",
                    tooltip=(
                        "<p>이 사진의 눈 뜬 정도까지는 정상으로 보고, 이보다 눈이 더 감긴 "
                        "사진을 C컷(눈감음)으로 판정합니다. 얼굴이 검출된 사진에서만 쓸 수 "
                        "있습니다.</p>"
                    ),
                    enabled=lambda row: not _unscored(row) and row.face is not None,
                    triggered=self._set_eyes_reference,
                ),
            ],
            verdict=lambda row: "판정: "
            + ("C컷" if row.selected else ("판정 불가" if _unscored(row) else "정상")),
            parent=self,
        )
        self._viewer.finished.connect(self._on_viewer_closed)
        self._viewer.show()

    def _on_viewer_closed(self) -> None:
        self._viewer = None

    def _rows_in_viewer_order(self) -> list[ReportRow]:
        by_path = {r.path: r for r in self._rows or []}
        return [by_path[p] for p in self._viewer_paths if p in by_path]

    def _reclassify(self) -> None:
        thresholds = BadCutThresholds(
            min_sharpness=self._sharpness_threshold,
            min_eyes_open=self._eyes_threshold,
        )
        rows = classify_badcut(self._photos, self._scores, thresholds)
        self._rows = rows
        bad_count = sum(1 for r in rows if r.selected)
        self._apply_button.setEnabled(bad_count > 0 and self._output_dir is not None)
        self._summary_text.setPlainText(format_badcut_header(rows))
        self._populate_table(rows)
        if self._viewer is not None:
            self._viewer.update_rows(self._rows_in_viewer_order())

    def _populate_table(self, rows: list[ReportRow]) -> None:
        selected = self._selected_row()
        # 채우는 도중에 정렬이 켜져 있으면 setItem마다 행이 재배치돼 칸이 뒤섞인다.
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            thumb_item = QTableWidgetItem()
            pixmap = self._thumbnail_pixmap(row.path)
            if pixmap is not None:
                thumb_item.setData(Qt.ItemDataRole.DecorationRole, pixmap)
            self._table.setItem(i, 0, thumb_item)
            name_item = QTableWidgetItem(row.path.name)
            name_item.setData(_PATH_ROLE, row.path)
            self._table.setItem(i, 1, name_item)
            self._table.setItem(i, 2, QTableWidgetItem("C컷" if row.selected else "정상"))
            sharpness_item = _NumberItem(row.sharpness, 1)
            sharpness_item.setToolTip(format_sharpness_region(row.sharpness_region))
            self._table.setItem(i, 3, sharpness_item)
            self._table.setItem(i, 4, _NumberItem(row.face, 2))
            self._table.setItem(i, 5, QTableWidgetItem(row.reason))
        self._table.setSortingEnabled(True)
        self._table.resizeRowsToContents()
        if selected is not None:
            self._reselect(selected.path)
        self._update_reference_buttons()

    def _reselect(self, path: Path) -> None:
        for i in range(self._table.rowCount()):
            if self._table.item(i, 1).data(_PATH_ROLE) == path:
                self._table.selectRow(i)
                return

    def _thumbnail_pixmap(self, path: Path) -> QPixmap | None:
        cached = self._thumbnail_cache.get(path)
        if cached is None or cached[2] is None:
            return None
        key = cached[:2]
        pixmap_entry = self._pixmap_cache.get(path)
        if pixmap_entry is not None and pixmap_entry[:2] == key:
            return pixmap_entry[2]
        qim = ImageQt(cached[2].convert("RGBA"))
        pixmap = QPixmap.fromImage(qim)
        self._pixmap_cache[path] = (*key, pixmap)
        return pixmap

    def _run_apply(self) -> None:
        if self._busy:
            QMessageBox.warning(
                self, "작업 진행 중", "다른 작업이 진행 중입니다. 완료될 때까지 기다려주세요."
            )
            return

        if not self._rows:
            QMessageBox.warning(self, "미리보기 필요", "먼저 미리보기를 실행하세요.")
            return

        if self._output_dir is None:
            QMessageBox.warning(self, "출력 폴더 없음", "출력 폴더를 먼저 선택하세요.")
            return

        bad_count = sum(1 for r in self._rows if r.selected)
        confirm = QMessageBox.question(
            self,
            "적용 확인",
            f"C컷으로 판정된 {bad_count}장(RAW 포함)을 {self._output_dir}(으)로 "
            "이동합니다 (복사 아님, 원본 위치엔 남지 않음).\n계속하시겠습니까?",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        self._busy = True
        self._apply_button.setEnabled(False)
        self._progress_bar.setValue(0)

        self._apply_worker = ApplyWorker(self._rows, self._output_dir)
        self._apply_worker.progress.connect(self._on_progress)
        self._apply_worker.finished_ok.connect(self._on_apply_done)
        self._apply_worker.failed.connect(self._on_error)
        self._apply_worker.start()

    def _on_apply_done(self, results: list[FileOpResult], report_path: Path) -> None:
        self._busy = False
        self._apply_button.setEnabled(False)
        # 이동된 파일은 더 이상 입력 폴더에 없으므로 캐시된 점수가 낡았다 —
        # 다시 정리하려면 미리보기를 새로 눌러야 한다.
        self._invalidate_preview()
        self._summary_text.setPlainText(format_apply_summary(results, self._output_dir, report_path))

    def _on_progress(self, done: int, total: int) -> None:
        self._progress_bar.setMaximum(total)
        self._progress_bar.setValue(done)

    def _cancel_preview(self) -> None:
        if self._score_worker is not None and self._score_worker.isRunning():
            self._score_worker.request_cancel()
            self._cancel_preview_button.setEnabled(False)
            self._status_label.setText("취소하는 중...")

    def _on_preview_cancelled(self) -> None:
        self._busy = False
        self._cancel_preview_button.setEnabled(False)
        self._preview_button.setEnabled(True)
        self._progress_bar.setValue(0)
        # 그때까지 계산한 점수는 캐시에 이미 들어가 있다 — 저장해 두면 다음에 이어서 계산된다.
        score_store.save()
        self._status_label.setText("미리보기를 취소했습니다 (그때까지 계산한 점수는 저장됨)")
        self._summary_text.setPlainText(
            "미리보기를 취소했습니다. 다시 미리보기를 누르면 이미 계산한 사진은 건너뛰고 이어서 "
            "계산합니다."
        )

    def _on_error(self, message: str) -> None:
        self._cancel_preview_button.setEnabled(False)
        self._busy = False
        self._preview_button.setEnabled(True)
        bad_count = sum(1 for r in (self._rows or []) if r.selected)
        self._apply_button.setEnabled(bad_count > 0 and self._output_dir is not None)
        QMessageBox.critical(self, "오류", message)


def _unscored(row: ReportRow) -> bool:
    return row.reason.startswith("판정 불가")


class _NumberItem(QTableWidgetItem):
    """문자열이 아니라 숫자 크기로 정렬되는 칸. 값이 없는(얼굴 미검출) 칸은 맨 뒤로."""

    def __init__(self, value: float | None, decimals: int) -> None:
        super().__init__("" if value is None else f"{value:.{decimals}f}")
        self._value = value

    def __lt__(self, other: QTableWidgetItem) -> bool:
        if not isinstance(other, _NumberItem):
            return super().__lt__(other)
        if self._value is None or other._value is None:
            return self._value is not None and other._value is None
        return self._value < other._value
