"""M8: 베스트컷(A컷) 선별 탭. gui.py에서 분리해 QTabWidget의 탭 하나로 얹는다.

장면 간격(몇 초 이내면 같은 장면인지)은 화면에서 조절한다(PROJECT_SPEC.md 4.2절
"N은 설정 가능한 값"). 미리보기로 한 번 스캔·스코어링해두면, 간격을 바꿀 때마다
사진을 다시 읽지 않고 캐시된 점수로 즉시 다시 묶는다.

장면을 어떻게 묶고 어떤 점수로 고르는지가 화면에 드러나지 않으면 결과를 믿고
쓰기 어려워, 설명 툴팁(ⓘ)과 사진별 점수 칸을 함께 보여준다.
"""

from __future__ import annotations

from pathlib import Path

from PIL.ImageQt import ImageQt
from PySide6.QtCore import QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from file_ops import FileOpResult, apply_selection
from grouping import DEFAULT_SCENE_GAP_SECONDS, group_by_time_gap
from gui_dnd import extract_dropped_folders
from gui_format import (
    bestcut_grouping_help,
    bestcut_scoring_help,
    format_apply_summary,
    format_preview_header,
    format_sharpness_region,
    sharpness_measure_help,
)
from gui_settings import (
    load_float,
    load_folder,
    load_folder_list,
    make_settings,
    save_float,
    save_folder,
    save_folder_list,
)
from report import ReportRow, build_report
from scanner import PhotoMetadata, scan_and_extract_many
from thumbnails import make_thumbnail


_TOTAL_TOOLTIP = (
    "<p><b>종합점수(0~1)</b>: 선명도·노출·눈뜸을 비율대로 합친 값입니다. "
    "장면 안에서 이 값이 가장 높은 컷이 선택됩니다.</p>"
)
_SHARPNESS_TOOLTIP = (
    "<p><b>선명도</b>: 경계선이 얼마나 또렷한지를 나타내는 원래 값입니다. "
    "종합점수에는 같은 장면 안에서 0~1로 바꿔 반영합니다(장면 안에서 가장 선명하면 "
    "1).</p>" + sharpness_measure_help()
)
_EXPOSURE_TOOLTIP = (
    "<p><b>노출(0~1)</b>: 완전히 까맣게 뭉개지거나 하얗게 날아간 픽셀이 적을수록 "
    "1에 가깝습니다.</p>"
)
_EYES_TOOLTIP = (
    "<p><b>눈뜸(0~1)</b>: 얼굴이 검출된 사진에서 눈을 뜬 정도입니다. 빈칸은 얼굴이 "
    "검출되지 않은 사진으로, 종합점수에서 이 항목을 빼고 계산합니다.</p>"
)


SCENE_GAP_MIN_SECONDS = 0.5
SCENE_GAP_MAX_SECONDS = 60.0
SCENE_GAP_SETTINGS_KEY = "bestcut/scene_gap_seconds"
# 스핀박스 화살표를 누르고 있으면 값이 연달아 바뀌므로, 마지막 변경 후 이만큼 조용하면
# 그때 한 번만 다시 묶는다.
REGROUP_DELAY_MS = 300


def _percent(done: int, total: int) -> int:
    return int(done / total * 100) if total else 0


class PreviewWorker(QThread):
    progress = Signal(int, int)
    status = Signal(str)
    finished_ok = Signal(list, list, float)  # list[PhotoMetadata], list[ReportRow], 간격(초)
    failed = Signal(str)

    def __init__(
        self,
        input_dirs: list[Path],
        gap_seconds: float = DEFAULT_SCENE_GAP_SECONDS,
        recursive: bool = True,
        scan_cache: dict | None = None,
        score_cache: dict | None = None,
        thumbnail_cache: dict | None = None,
    ) -> None:
        super().__init__()
        self._input_dirs = input_dirs
        self._gap_seconds = gap_seconds
        self._recursive = recursive
        self._scan_cache = scan_cache
        self._score_cache = score_cache
        self._thumbnail_cache = thumbnail_cache

    def run(self) -> None:
        try:
            def on_scan_progress(done: int, total: int, path: Path) -> None:
                self.progress.emit(done, total)
                self.status.emit(f"스캔 중: {path.name} ({done}/{total}, {_percent(done, total)}%)")

            photos = scan_and_extract_many(
                self._input_dirs,
                recursive=self._recursive,
                on_progress=on_scan_progress,
                cache=self._scan_cache,
            )
            groups = group_by_time_gap(photos, gap_seconds=self._gap_seconds)

            def on_group_progress(done: int, total: int) -> None:
                self.progress.emit(done, total)
                self.status.emit(f"장면 그룹핑/스코어링 중... ({done}/{total}, {_percent(done, total)}%)")

            rows = build_report(groups, on_progress=on_group_progress, cache=self._score_cache)
            self._generate_thumbnails(rows)
            self.finished_ok.emit(photos, rows, self._gap_seconds)
        except Exception as e:  # noqa: BLE001 - surfaced to the user, not swallowed
            self.failed.emit(str(e))

    def _generate_thumbnails(self, rows: list[ReportRow]) -> None:
        if self._thumbnail_cache is None:
            return
        for row in rows:
            try:
                stat = row.path.stat()
            except OSError:
                continue
            key = (stat.st_mtime, stat.st_size)
            cached = self._thumbnail_cache.get(row.path)
            if cached is not None and cached[:2] == key:
                continue
            self._thumbnail_cache[row.path] = (*key, make_thumbnail(row.path))


class ApplyWorker(QThread):
    progress = Signal(int, int)
    finished_ok = Signal(list)  # list[FileOpResult]
    failed = Signal(str)

    def __init__(self, rows: list[ReportRow], output_dir: Path, move: bool) -> None:
        super().__init__()
        self._rows = rows
        self._output_dir = output_dir
        self._move = move

    def run(self) -> None:
        try:
            results = apply_selection(
                self._rows,
                self._output_dir,
                move=self._move,
                on_progress=self.progress.emit,
            )
            self.finished_ok.emit(results)
        except Exception as e:  # noqa: BLE001 - surfaced to the user, not swallowed
            self.failed.emit(str(e))


class BestCutTab(QWidget):
    def __init__(self) -> None:
        super().__init__()

        self._input_dirs: list[Path] = []
        self._output_dir: Path | None = None
        self._rows: list[ReportRow] | None = None
        # 마지막 미리보기에서 스캔한 사진. 간격만 바꿔 다시 묶을 때 재사용한다.
        self._photos: list[PhotoMetadata] | None = None
        self._rows_gap_seconds: float | None = None
        self._preview_worker: PreviewWorker | None = None
        self._apply_worker: ApplyWorker | None = None
        self._busy = False
        # 같은 세션(앱 종료 전까지) 동안 이미 스캔/스코어링한 파일은 다시 처리하지
        # 않도록 재사용하는 인메모리 캐시. (mtime, size)가 바뀐 파일만 다시 계산됨.
        self._scan_cache: dict = {}
        self._score_cache: dict = {}
        self._thumbnail_cache: dict = {}
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

        self._copy_radio = QRadioButton("복사 (기본)")
        self._copy_radio.setChecked(True)
        self._move_radio = QRadioButton("이동")

        self._preview_button = QPushButton("미리보기")
        self._preview_button.setToolTip(
            "<p>사진을 장면별로 묶고 장면마다 베스트컷 1장을 고른 결과를 보여줍니다. "
            "파일은 아직 복사/이동하지 않습니다.</p>"
        )
        self._preview_button.clicked.connect(self._run_preview)

        self._gap_spin = QDoubleSpinBox()
        self._gap_spin.setRange(SCENE_GAP_MIN_SECONDS, SCENE_GAP_MAX_SECONDS)
        self._gap_spin.setDecimals(1)
        self._gap_spin.setSingleStep(0.5)
        self._gap_spin.setSuffix(" 초")
        self._gap_spin.setValue(
            load_float(
                self._settings,
                SCENE_GAP_SETTINGS_KEY,
                DEFAULT_SCENE_GAP_SECONDS,
                SCENE_GAP_MIN_SECONDS,
                SCENE_GAP_MAX_SECONDS,
            )
        )
        self._regroup_timer = QTimer(self)
        self._regroup_timer.setSingleShot(True)
        self._regroup_timer.setInterval(REGROUP_DELAY_MS)
        self._regroup_timer.timeout.connect(self._regroup)
        self._gap_spin.valueChanged.connect(self._on_gap_changed)
        gap_label = QLabel("장면 간격 ⓘ:")
        self._gap_widgets = (gap_label, self._gap_spin)

        self._grouping_help_label = QLabel("ⓘ 장면은 어떻게 묶나요?")
        scoring_help_label = QLabel("ⓘ 베스트컷은 어떻게 고르나요?")
        scoring_help_label.setToolTip(bestcut_scoring_help())
        help_row = QHBoxLayout()
        help_row.addWidget(gap_label)
        help_row.addWidget(self._gap_spin)
        help_row.addWidget(self._grouping_help_label)
        help_row.addWidget(scoring_help_label)
        help_row.addStretch()

        self._apply_button = QPushButton("적용")
        self._apply_button.setEnabled(False)
        self._apply_button.clicked.connect(self._run_apply)

        self._open_output_button = QPushButton("출력 폴더 열기")
        self._open_output_button.setEnabled(False)
        self._open_output_button.clicked.connect(self._open_output_folder)

        self._progress_bar = QProgressBar()
        self._status_label = QLabel("")
        self._summary_text = QPlainTextEdit()
        self._summary_text.setReadOnly(True)
        self._summary_text.setAcceptDrops(False)

        self._table = QTableWidget(0, 9)
        self._table.setHorizontalHeaderLabels(
            ["썸네일", "파일명", "장면", "선택여부", "종합점수", "선명도", "노출", "눈뜸", "사유"]
        )
        for column, tooltip in (
            (3, bestcut_scoring_help()),
            (4, _TOTAL_TOOLTIP),
            (5, _SHARPNESS_TOOLTIP),
            (6, _EXPOSURE_TOOLTIP),
            (7, _EYES_TOOLTIP),
            (8, bestcut_scoring_help()),
        ):
            self._table.horizontalHeaderItem(column).setToolTip(tooltip)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setIconSize(QSize(64, 64))

        radio_row = QHBoxLayout()
        radio_row.addWidget(self._copy_radio)
        radio_row.addWidget(self._move_radio)

        input_button_row = QHBoxLayout()
        input_button_row.addWidget(add_input_button)
        input_button_row.addWidget(remove_input_button)

        button_row = QHBoxLayout()
        button_row.addWidget(self._preview_button)
        button_row.addWidget(self._apply_button)
        button_row.addWidget(self._open_output_button)

        layout = QVBoxLayout()
        layout.addWidget(QLabel("입력 폴더 (여러 개 추가 가능):"))
        layout.addWidget(self._folder_list)
        layout.addLayout(input_button_row)
        layout.addWidget(self._output_label)
        layout.addWidget(output_button)
        layout.addLayout(radio_row)
        layout.addLayout(help_row)
        layout.addLayout(button_row)
        layout.addWidget(self._progress_bar)
        layout.addWidget(self._status_label)
        layout.addWidget(self._summary_text)
        layout.addWidget(self._table)

        for folder in load_folder_list(self._settings, "bestcut/input_dirs"):
            self._input_dirs.append(folder)
            self._folder_list.addItem(str(folder))
        restored_output = load_folder(self._settings, "bestcut/output_dir")
        if restored_output is not None:
            self._output_dir = restored_output
            self._output_label.setText(f"출력 폴더: {restored_output}")

        self._update_grouping_help()
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
        save_folder_list(self._settings, "bestcut/input_dirs", self._input_dirs)

    def _remove_selected_input_dir(self) -> None:
        for item in self._folder_list.selectedItems():
            index = self._folder_list.row(item)
            self._folder_list.takeItem(index)
            del self._input_dirs[index]
        self._invalidate_preview()
        save_folder_list(self._settings, "bestcut/input_dirs", self._input_dirs)

    def _choose_output_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "출력 폴더 선택")
        if directory:
            self._output_dir = Path(directory)
            self._output_label.setText(f"출력 폴더: {directory}")
            # 미리보기 결과(스캔/장면 그룹핑/스코어링)는 출력 폴더와 무관하므로
            # 다시 계산할 필요 없음 — 적용 버튼 활성화 여부만 갱신한다.
            self._apply_button.setEnabled(bool(self._rows))
            self._open_output_button.setEnabled(False)
            save_folder(self._settings, "bestcut/output_dir", self._output_dir)

    def _invalidate_preview(self) -> None:
        self._rows = None
        self._photos = None
        self._apply_button.setEnabled(False)
        self._open_output_button.setEnabled(False)
        self._table.setRowCount(0)

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
        self._summary_text.setPlainText("스캔/스코어링 중...")

        self._preview_worker = PreviewWorker(
            list(self._input_dirs),
            gap_seconds=self._gap_spin.value(),
            scan_cache=self._scan_cache,
            score_cache=self._score_cache,
            thumbnail_cache=self._thumbnail_cache,
        )
        self._preview_worker.progress.connect(self._on_progress)
        self._preview_worker.status.connect(self._status_label.setText)
        self._preview_worker.finished_ok.connect(self._on_preview_done)
        self._preview_worker.failed.connect(self._on_error)
        self._preview_worker.start()

    def _on_preview_done(
        self, photos: list[PhotoMetadata], rows: list[ReportRow], gap_seconds: float
    ) -> None:
        self._busy = False
        self._status_label.setText("완료")
        self._photos = photos
        self._preview_button.setEnabled(True)
        self._show_rows(rows, gap_seconds)
        # 미리보기가 도는 사이에 간격을 바꿨으면, 끝난 뒤 새 간격으로 다시 묶는다.
        if gap_seconds != self._gap_spin.value():
            self._regroup_timer.start()

    def _show_rows(self, rows: list[ReportRow], gap_seconds: float) -> None:
        self._rows = rows
        self._rows_gap_seconds = gap_seconds
        self._apply_button.setEnabled(bool(rows) and self._output_dir is not None)
        self._summary_text.setPlainText(format_preview_header(rows, gap_seconds))
        self._populate_table(rows)

    def _on_gap_changed(self, value: float) -> None:
        save_float(self._settings, SCENE_GAP_SETTINGS_KEY, value)
        self._update_grouping_help()
        # 아직 미리보기 전이면 다음 미리보기 때 이 값으로 묶인다.
        if self._photos is not None:
            self._regroup_timer.start()

    def _regroup(self) -> None:
        """캐시된 점수로 새 간격에 맞춰 장면을 다시 묶고 베스트컷을 다시 고른다.
        스캔·스코어링을 다시 하지 않아 즉시 끝난다."""
        if self._busy:
            # 미리보기/적용이 끝나면 _on_preview_done이 간격 차이를 보고 다시 부른다.
            return
        if self._photos is None:
            return
        gap_seconds = self._gap_spin.value()
        if gap_seconds == self._rows_gap_seconds:
            return
        try:
            groups = group_by_time_gap(self._photos, gap_seconds=gap_seconds)
            rows = build_report(groups, cache=self._score_cache)
        except OSError as e:
            # 미리보기 뒤에 파일이 옮겨졌거나 지워진 경우 — 새로 미리보기해야 한다.
            self._invalidate_preview()
            QMessageBox.warning(
                self,
                "다시 미리보기 필요",
                f"미리보기 뒤에 사진 파일이 바뀌어 다시 묶을 수 없습니다. 미리보기를 다시 "
                f"눌러주세요.\n({e})",
            )
            return
        self._show_rows(rows, gap_seconds)
        self._status_label.setText(f"장면 간격 {gap_seconds:g}초로 다시 묶음")

    def _update_grouping_help(self) -> None:
        help_text = bestcut_grouping_help(self._gap_spin.value())
        for widget in self._gap_widgets:
            widget.setToolTip(help_text)
        self._grouping_help_label.setToolTip(help_text)
        self._table.horizontalHeaderItem(2).setToolTip(help_text)

    def _populate_table(self, rows: list[ReportRow]) -> None:
        self._table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            thumb_item = QTableWidgetItem()
            pixmap = self._thumbnail_pixmap(row.path)
            if pixmap is not None:
                thumb_item.setData(Qt.ItemDataRole.DecorationRole, pixmap)
            self._table.setItem(i, 0, thumb_item)
            self._table.setItem(i, 1, QTableWidgetItem(row.path.name))
            self._table.setItem(i, 2, QTableWidgetItem(str(row.scene_id)))
            self._table.setItem(i, 3, QTableWidgetItem("선택" if row.selected else "제외"))
            self._table.setItem(i, 4, QTableWidgetItem(f"{row.total:.2f}"))
            sharpness_item = QTableWidgetItem(f"{row.sharpness:.1f}")
            sharpness_item.setToolTip(format_sharpness_region(row.sharpness_region))
            self._table.setItem(i, 5, sharpness_item)
            self._table.setItem(i, 6, QTableWidgetItem(f"{row.exposure:.2f}"))
            self._table.setItem(i, 7, QTableWidgetItem("" if row.face is None else f"{row.face:.2f}"))
            self._table.setItem(i, 8, QTableWidgetItem(row.reason))
        self._table.resizeRowsToContents()

    def _thumbnail_pixmap(self, path: Path) -> QPixmap | None:
        cached = self._thumbnail_cache.get(path)
        if cached is None or cached[2] is None:
            return None
        qim = ImageQt(cached[2].convert("RGBA"))
        return QPixmap.fromImage(qim)

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

        move = self._move_radio.isChecked()
        verb = "이동" if move else "복사"
        confirm = QMessageBox.question(
            self,
            "적용 확인",
            f"선택된 사진을 {self._output_dir}(으)로 {verb}하시겠습니까?",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        self._busy = True
        self._apply_button.setEnabled(False)
        self._progress_bar.setValue(0)

        self._apply_worker = ApplyWorker(self._rows, self._output_dir, move)
        self._apply_worker.progress.connect(self._on_progress)
        self._apply_worker.finished_ok.connect(self._on_apply_done)
        self._apply_worker.failed.connect(self._on_error)
        self._apply_worker.start()

    def _on_apply_done(self, results: list[FileOpResult]) -> None:
        self._busy = False
        if self._move_radio.isChecked():
            # 옮긴 파일은 더 이상 입력 폴더에 없어, 간격을 바꿔 다시 묶을 수 없다.
            self._photos = None
        elif self._photos is not None and self._gap_spin.value() != self._rows_gap_seconds:
            # 적용하는 사이에 간격을 바꿨으면 이제 다시 묶는다.
            self._regroup_timer.start()
        self._apply_button.setEnabled(True)
        self._open_output_button.setEnabled(True)
        self._summary_text.setPlainText(
            format_apply_summary(results, self._output_dir)
        )

    def _open_output_folder(self) -> None:
        if self._output_dir is not None:
            # macOS/Windows 공통으로 OS 기본 파일 탐색기에서 연다
            opened = QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(self._output_dir))
            )
            if not opened:
                QMessageBox.warning(
                    self,
                    "폴더 열기 실패",
                    f"출력 폴더를 열지 못했습니다: {self._output_dir}",
                )

    def _on_progress(self, done: int, total: int) -> None:
        self._progress_bar.setMaximum(total)
        self._progress_bar.setValue(done)

    def _on_error(self, message: str) -> None:
        self._busy = False
        self._preview_button.setEnabled(True)
        self._apply_button.setEnabled(bool(self._rows) and self._output_dir is not None)
        QMessageBox.critical(self, "오류", message)
