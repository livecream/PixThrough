"""M8: 베스트컷(A컷) 선별 탭. gui.py에서 분리해 QTabWidget의 탭 하나로 얹는다.

장면 간격(몇 초 이내면 같은 장면인지)은 화면에서 조절한다(PROJECT_SPEC.md 4.2절
"N은 설정 가능한 값"). 미리보기로 한 번 스캔·스코어링해두면, 간격을 바꿀 때마다
사진을 다시 읽지 않고 캐시된 점수로 즉시 다시 묶는다.

"비슷하지 않은 사진 나누기"(기본 꺼짐)를 켜면, 시간상 같은 장면이어도 바로 앞 사진과
이미지 지문(pHash) 차이가 사용자가 정한 값을 넘는 곳에서 장면을 나눈다. 지문은 미리보기
때 항상 계산해두므로, 켜고 끄거나 기준을 바꿔도 즉시 다시 묶인다. 기준을 고르는 데
참고하도록 표에 "앞 사진과 차이" 칸을 보여준다.

장면을 어떻게 묶고 어떤 점수로 고르는지가 화면에 드러나지 않으면 결과를 믿고
쓰기 어려워, 설명 툴팁(ⓘ)과 사진별 점수 칸을 함께 보여준다.

자동 선정(장면당 1장)이 마음에 들지 않으면 사용자가 컷을 직접 더 넣거나 뺄 수 있다.
직접 고른 것은 경로별로 따로 들고 있다가 자동 결과에 덮어쓰므로(report.
apply_manual_choices), 장면 간격을 바꿔 다시 묶어도 유지된다.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from PIL.ImageQt import ImageQt
from PySide6.QtCore import QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
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
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from file_ops import FileOpResult, apply_selection
from grouping import (
    DEFAULT_SCENE_GAP_SECONDS,
    distances_to_previous,
    group_by_time_gap,
    split_dissimilar,
)
from gui_dnd import extract_dropped_folders
from gui_format import (
    bestcut_grouping_help,
    bestcut_scoring_help,
    format_apply_summary,
    format_manual_choice_summary,
    format_preview_header,
    format_sharpness_region,
    sharpness_measure_help,
)
from gui_settings import (
    load_bool,
    load_float,
    load_folder,
    load_folder_list,
    make_settings,
    save_bool,
    save_float,
    save_folder,
    save_folder_list,
)
from gui_photo_viewer import PhotoViewerDialog, ViewerAction
from image_hash import HASH_BITS, hashes_for
from report import ReportRow, apply_manual_choices, build_report
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
SPLIT_ENABLED_SETTINGS_KEY = "bestcut/split_dissimilar"
SPLIT_DISTANCE_SETTINGS_KEY = "bestcut/split_hash_distance"
# 나누기 기준 입력칸을 처음 켤 때의 시작값. 기능 자체는 기본으로 꺼져 있어 이 값이
# 몰래 적용되지는 않는다. 공개 샘플 측정(같은 사진 변형 0~12, 다른 사진 28~36)의 중간.
SPLIT_DISTANCE_START = 20
SPLIT_DISTANCE_MIN = 1
SPLIT_DISTANCE_MAX = HASH_BITS - 1
KEEP_PERCENT_SETTINGS_KEY = "bestcut/keep_percent"


class _SelectionSettings(NamedTuple):
    """미리보기 결과를 만든 기준. 바뀌면 캐시된 점수로 다시 묶고 다시 고른다."""

    gap_seconds: float
    split_distance: int | None  # 비슷하지 않은 사진 나누기 기준, 꺼져 있으면 None
    keep_percent: int  # 장면마다 남길 비율(%), 0이면 1장
# 스핀박스 화살표를 누르고 있으면 값이 연달아 바뀌므로, 마지막 변경 후 이만큼 조용하면
# 그때 한 번만 다시 묶는다.
REGROUP_DELAY_MS = 300


def _describe_choice(row: ReportRow) -> str:
    """크게 보기 창 위쪽에 보여줄 이 컷의 상태."""
    state = "선택" if row.selected else "제외"
    if row.manual_choice is not None:
        state += "(직접)"
    return f"장면 {row.scene_id} · {state} · 종합점수 {row.total:.2f}"


_DIFF_TOOLTIP = (
    "<p><b>앞 사진과 차이(0~64)</b>: 촬영 시각순으로 바로 앞 사진과 얼마나 다르게 "
    "생겼는지입니다. 사진을 아주 작게 줄여 만든 지문(pHash)을 비교한 값이라 색·밝기·"
    "약간의 흔들림에는 둔하고, 구도·피사체가 바뀌면 커집니다.</p>"
    "<p>공개 샘플로 재 보면 같은 사진을 조금 옮기거나 밝게 한 것은 0~12, 서로 다른 "
    "사진은 28~36 정도였습니다. ✂ 표시는 나누기를 켰을 때 이 값이 기준을 넘어 장면이 "
    "나뉘는 자리입니다.</p>"
)


def _make_groups(
    photos: list[PhotoMetadata],
    hashes: dict[Path, int | None],
    gap_seconds: float,
    split_distance: int | None,
) -> list[list[PhotoMetadata]]:
    groups = group_by_time_gap(photos, gap_seconds=gap_seconds)
    if split_distance is None:
        return groups
    return split_dissimilar(groups, hashes, split_distance)


def _percent(done: int, total: int) -> int:
    return int(done / total * 100) if total else 0


class PreviewWorker(QThread):
    progress = Signal(int, int)
    status = Signal(str)
    # list[PhotoMetadata], list[ReportRow], _SelectionSettings, {경로: 지문}
    finished_ok = Signal(list, list, object, object)
    failed = Signal(str)

    def __init__(
        self,
        input_dirs: list[Path],
        settings: _SelectionSettings = _SelectionSettings(DEFAULT_SCENE_GAP_SECONDS, None, 0),
        recursive: bool = True,
        scan_cache: dict | None = None,
        score_cache: dict | None = None,
        thumbnail_cache: dict | None = None,
        hash_cache: dict | None = None,
    ) -> None:
        super().__init__()
        self._input_dirs = input_dirs
        self._selection_settings = settings
        self._hash_cache = hash_cache
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
            def on_hash_progress(done: int, total: int) -> None:
                self.progress.emit(done, total)
                self.status.emit(f"사진 유사도 계산 중... ({done}/{total}, {_percent(done, total)}%)")

            # 나누기를 꺼둔 상태여도 계산해둔다 — 나중에 켜면 즉시 다시 묶을 수 있게.
            hashes = hashes_for(
                [p.path for p in photos], cache=self._hash_cache, on_progress=on_hash_progress
            )
            settings = self._selection_settings
            groups = _make_groups(photos, hashes, settings.gap_seconds, settings.split_distance)

            def on_group_progress(done: int, total: int) -> None:
                self.progress.emit(done, total)
                self.status.emit(f"장면 그룹핑/스코어링 중... ({done}/{total}, {_percent(done, total)}%)")

            rows = build_report(
                groups,
                on_progress=on_group_progress,
                cache=self._score_cache,
                keep_percent=settings.keep_percent,
            )
            self._generate_thumbnails(rows)
            self.finished_ok.emit(photos, rows, settings, hashes)
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
        # 자동 선정 결과(직접 고른 것 반영 전)와, 사용자가 직접 고른 것(경로 → 선택 여부).
        # 화면·적용에 쓰는 self._rows는 둘을 합친 결과다.
        self._auto_rows: list[ReportRow] | None = None
        self._choices: dict[Path, bool] = {}
        self._viewer: PhotoViewerDialog | None = None
        self._viewer_paths: list[Path] = []
        # 마지막 미리보기에서 스캔한 사진. 간격만 바꿔 다시 묶을 때 재사용한다.
        self._photos: list[PhotoMetadata] | None = None
        # 지금 화면의 결과를 만든 기준
        self._rows_grouping: _SelectionSettings | None = None
        self._hashes: dict[Path, int | None] = {}
        self._distances: dict[Path, int | None] = {}
        self._preview_worker: PreviewWorker | None = None
        self._apply_worker: ApplyWorker | None = None
        self._busy = False
        # 같은 세션(앱 종료 전까지) 동안 이미 스캔/스코어링한 파일은 다시 처리하지
        # 않도록 재사용하는 인메모리 캐시. (mtime, size)가 바뀐 파일만 다시 계산됨.
        self._scan_cache: dict = {}
        self._score_cache: dict = {}
        self._thumbnail_cache: dict = {}
        self._hash_cache: dict = {}
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

        self._split_checkbox = QCheckBox("비슷하지 않은 사진은 다른 장면으로 나누기")
        self._split_checkbox.setChecked(load_bool(self._settings, SPLIT_ENABLED_SETTINGS_KEY, False))
        self._split_spin = QSpinBox()
        self._split_spin.setRange(SPLIT_DISTANCE_MIN, SPLIT_DISTANCE_MAX)
        self._split_spin.setPrefix("앞 사진과 차이가 ")
        self._split_spin.setSuffix(" 넘으면")
        self._split_spin.setValue(
            int(
                load_float(
                    self._settings,
                    SPLIT_DISTANCE_SETTINGS_KEY,
                    SPLIT_DISTANCE_START,
                    SPLIT_DISTANCE_MIN,
                    SPLIT_DISTANCE_MAX,
                )
            )
        )
        self._split_spin.setEnabled(self._split_checkbox.isChecked())
        self._split_checkbox.toggled.connect(self._on_split_changed)
        self._split_spin.valueChanged.connect(self._on_split_changed)
        split_row = QHBoxLayout()
        split_row.addWidget(self._split_checkbox)
        split_row.addWidget(self._split_spin)
        split_row.addStretch()

        self._keep_spin = QSpinBox()
        self._keep_spin.setRange(0, 100)
        self._keep_spin.setSingleStep(5)
        self._keep_spin.setSuffix(" %")
        self._keep_spin.setValue(
            int(load_float(self._settings, KEEP_PERCENT_SETTINGS_KEY, 0, 0, 100))
        )
        self._keep_spin.valueChanged.connect(self._on_keep_changed)
        keep_label = QLabel("장면마다 남길 비율 ⓘ:")
        self._keep_hint = QLabel()
        self._keep_widgets = (keep_label, self._keep_spin, self._keep_hint)
        split_row.insertWidget(0, keep_label)
        split_row.insertWidget(1, self._keep_spin)
        split_row.insertWidget(2, self._keep_hint)

        self._grouping_help_label = QLabel("ⓘ 장면은 어떻게 묶나요?")
        self._scoring_help_label = QLabel("ⓘ 베스트컷은 어떻게 고르나요?")
        help_row = QHBoxLayout()
        help_row.addWidget(gap_label)
        help_row.addWidget(self._gap_spin)
        help_row.addWidget(self._grouping_help_label)
        help_row.addWidget(self._scoring_help_label)
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

        self._table = QTableWidget(0, 10)
        self._table.setHorizontalHeaderLabels(
            [
                "썸네일",
                "파일명",
                "장면",
                "앞 사진과 차이",
                "선택여부",
                "종합점수",
                "선명도",
                "노출",
                "눈뜸",
                "사유",
            ]
        )
        for column, tooltip in (
            (3, _DIFF_TOOLTIP),
            (5, _TOTAL_TOOLTIP),
            (6, _SHARPNESS_TOOLTIP),
            (7, _EXPOSURE_TOOLTIP),
            (8, _EYES_TOOLTIP),
        ):
            self._table.horizontalHeaderItem(column).setToolTip(tooltip)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setIconSize(QSize(64, 64))
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.itemSelectionChanged.connect(self._update_choice_buttons)
        self._table.cellDoubleClicked.connect(self._open_viewer)

        self._view_button = QPushButton("크게 보기")
        self._view_button.setToolTip(
            "<p>선택한 사진을 크게 엽니다. 같은 장면의 컷끼리 ←/→로 넘겨보며 고를 수 "
            "있습니다. 표에서 사진을 더블클릭해도 됩니다.</p>"
        )
        self._view_button.clicked.connect(self._open_viewer_for_selected)
        self._toggle_button = QPushButton("선택에 넣기")
        self._toggle_button.setToolTip(
            "<p>표에서 고른 사진을 선택에 넣거나 뺍니다. 장면마다 자동으로 1장을 고르지만, "
            "원하는 컷을 더 넣거나 자동으로 고른 컷을 뺄 수 있습니다.</p>"
        )
        self._toggle_button.clicked.connect(self._toggle_selected_row)
        self._reset_choices_button = QPushButton("자동 선택으로 되돌리기")
        self._reset_choices_button.setToolTip("<p>직접 넣거나 뺀 것을 모두 취소합니다.</p>")
        self._reset_choices_button.clicked.connect(self._reset_choices)
        choice_button_row = QHBoxLayout()
        choice_button_row.addWidget(self._view_button)
        choice_button_row.addWidget(self._toggle_button)
        choice_button_row.addWidget(self._reset_choices_button)
        choice_button_row.addStretch()

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
        layout.addLayout(split_row)
        layout.addLayout(button_row)
        layout.addWidget(self._progress_bar)
        layout.addWidget(self._status_label)
        layout.addWidget(self._summary_text)
        layout.addWidget(
            QLabel("사진을 더블클릭하면 크게 볼 수 있고, 같은 장면 컷끼리 비교하며 고를 수 있습니다.")
        )
        layout.addLayout(choice_button_row)
        layout.addWidget(self._table)

        for folder in load_folder_list(self._settings, "bestcut/input_dirs"):
            self._input_dirs.append(folder)
            self._folder_list.addItem(str(folder))
        restored_output = load_folder(self._settings, "bestcut/output_dir")
        if restored_output is not None:
            self._output_dir = restored_output
            self._output_label.setText(f"출력 폴더: {restored_output}")

        self._update_grouping_help()
        self._update_choice_buttons()
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
        self._auto_rows = None
        self._photos = None
        self._close_viewer()
        self._apply_button.setEnabled(False)
        self._open_output_button.setEnabled(False)
        self._table.setRowCount(0)
        self._update_choice_buttons()

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
            settings=self._current_grouping(),
            scan_cache=self._scan_cache,
            score_cache=self._score_cache,
            thumbnail_cache=self._thumbnail_cache,
            hash_cache=self._hash_cache,
        )
        self._preview_worker.progress.connect(self._on_progress)
        self._preview_worker.status.connect(self._status_label.setText)
        self._preview_worker.finished_ok.connect(self._on_preview_done)
        self._preview_worker.failed.connect(self._on_error)
        self._preview_worker.start()

    def _on_preview_done(
        self,
        photos: list[PhotoMetadata],
        rows: list[ReportRow],
        grouping: _SelectionSettings,
        hashes: dict[Path, int | None],
    ) -> None:
        self._busy = False
        self._status_label.setText("완료")
        self._photos = photos
        self._hashes = hashes
        self._distances = distances_to_previous(photos, hashes)
        self._preview_button.setEnabled(True)
        self._show_rows(rows, grouping)
        # 미리보기가 도는 사이에 묶는 기준을 바꿨으면, 끝난 뒤 새 기준으로 다시 묶는다.
        if grouping != self._current_grouping():
            self._regroup_timer.start()

    def _current_grouping(self) -> _SelectionSettings:
        split = self._split_spin.value() if self._split_checkbox.isChecked() else None
        return _SelectionSettings(self._gap_spin.value(), split, self._keep_spin.value())

    def _show_rows(self, rows: list[ReportRow], grouping: _SelectionSettings) -> None:
        """새 자동 선정 결과(미리보기 또는 다시 묶기)를 받아 화면에 반영한다."""
        self._auto_rows = rows
        self._rows_grouping = grouping
        # 장면 번호가 바뀌었을 수 있어, 열려 있던 크게 보기 창(한 장면의 컷 목록)은 닫는다.
        self._close_viewer()
        self._refresh_selection()

    def _refresh_selection(self) -> None:
        """자동 결과에 직접 고른 것을 덮어써 화면·적용 대상을 갱신한다."""
        selected_row = self._selected_table_row()
        rows = apply_manual_choices(self._auto_rows or [], self._choices)
        self._rows = rows
        self._apply_button.setEnabled(any(r.selected for r in rows) and self._output_dir is not None)
        settings = self._rows_grouping
        summary = (
            format_preview_header(rows, *settings) if settings else format_preview_header(rows)
        )
        manual = format_manual_choice_summary(rows)
        self._summary_text.setPlainText(summary + (f"\n{manual}" if manual else ""))
        self._populate_table(rows)
        if selected_row is not None and selected_row < len(rows):
            self._table.selectRow(selected_row)
        self._update_choice_buttons()
        if self._viewer is not None:
            self._viewer.update_rows(self._rows_for_paths(self._viewer_paths))

    def _selected_table_row(self) -> int | None:
        selected = self._table.selectionModel().selectedRows()
        return selected[0].row() if selected else None

    def _update_choice_buttons(self) -> None:
        index = self._selected_table_row()
        row = self._rows[index] if self._rows and index is not None else None
        self._view_button.setEnabled(row is not None)
        self._toggle_button.setEnabled(row is not None)
        self._toggle_button.setText("선택에서 빼기" if row is not None and row.selected else "선택에 넣기")
        self._reset_choices_button.setEnabled(
            any(r.manual_choice is not None for r in self._rows or [])
        )

    def _toggle(self, row: ReportRow) -> None:
        """이 컷의 선택 여부를 뒤집는다. 뒤집은 결과가 자동 선정과 같으면 직접 고른
        기록을 지워(자동 그대로), 다시 묶을 때 쓸데없는 기록이 남지 않게 한다."""
        auto_selected = next(r.selected for r in self._auto_rows if r.path == row.path)
        wanted = not row.selected
        if wanted == auto_selected:
            self._choices.pop(row.path, None)
        else:
            self._choices[row.path] = wanted
        self._refresh_selection()

    def _toggle_selected_row(self) -> None:
        index = self._selected_table_row()
        if self._rows and index is not None:
            self._toggle(self._rows[index])

    def _reset_choices(self) -> None:
        self._choices.clear()
        self._refresh_selection()

    def _rows_for_paths(self, paths: list[Path]) -> list[ReportRow]:
        by_path = {r.path: r for r in self._rows or []}
        return [by_path[p] for p in paths if p in by_path]

    def _open_viewer_for_selected(self) -> None:
        index = self._selected_table_row()
        if index is not None:
            self._open_viewer(index)

    def _open_viewer(self, table_row: int, _column: int = 0) -> None:
        """클릭한 사진이 속한 장면의 컷만 모아 크게 보기 창을 연다(←/→로 장면 안에서 비교)."""
        if not self._rows:
            return
        clicked = self._rows[table_row]
        scene_rows = [r for r in self._rows if r.scene_id == clicked.scene_id]
        self._viewer_paths = [r.path for r in scene_rows]
        self._close_viewer()
        self._viewer = PhotoViewerDialog(
            scene_rows,
            scene_rows.index(clicked),
            actions=[
                ViewerAction(
                    label=lambda row: "이 컷 선택에서 빼기" if row.selected else "이 컷 선택에 넣기",
                    tooltip=(
                        "<p>이 컷을 선택에 넣거나 뺍니다. 한 장면에서 여러 컷을 넣어도 되고, "
                        "자동으로 고른 컷을 빼도 됩니다.</p>"
                    ),
                    enabled=lambda row: True,
                    triggered=self._toggle,
                )
            ],
            verdict=_describe_choice,
            parent=self,
        )
        self._viewer.finished.connect(self._on_viewer_closed)
        self._viewer.show()

    def _close_viewer(self) -> None:
        if self._viewer is not None:
            self._viewer.close()
            self._viewer = None

    def _on_viewer_closed(self) -> None:
        self._viewer = None

    def _on_gap_changed(self, value: float) -> None:
        save_float(self._settings, SCENE_GAP_SETTINGS_KEY, value)
        self._on_grouping_changed()

    def _on_split_changed(self) -> None:
        save_bool(self._settings, SPLIT_ENABLED_SETTINGS_KEY, self._split_checkbox.isChecked())
        save_float(self._settings, SPLIT_DISTANCE_SETTINGS_KEY, self._split_spin.value())
        self._split_spin.setEnabled(self._split_checkbox.isChecked())
        self._on_grouping_changed()

    def _on_keep_changed(self, value: int) -> None:
        save_float(self._settings, KEEP_PERCENT_SETTINGS_KEY, value)
        self._on_grouping_changed()

    def _on_grouping_changed(self) -> None:
        self._update_grouping_help()
        # 아직 미리보기 전이면 다음 미리보기 때 이 기준으로 묶인다.
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
        grouping = self._current_grouping()
        if grouping == self._rows_grouping:
            return
        try:
            groups = _make_groups(
                self._photos, self._hashes, grouping.gap_seconds, grouping.split_distance
            )
            rows = build_report(groups, cache=self._score_cache, keep_percent=grouping.keep_percent)
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
        self._show_rows(rows, grouping)
        self._status_label.setText("바뀐 기준으로 다시 묶고 다시 골랐습니다")

    def _update_grouping_help(self) -> None:
        settings = self._current_grouping()
        scoring_help = bestcut_scoring_help(keep_percent=settings.keep_percent)
        self._scoring_help_label.setToolTip(scoring_help)
        self._table.horizontalHeaderItem(4).setToolTip(scoring_help)
        self._table.horizontalHeaderItem(9).setToolTip(scoring_help)
        for widget in self._keep_widgets:
            widget.setToolTip(scoring_help)
        self._keep_hint.setText(
            "(장면마다 1장)"
            if not settings.keep_percent
            else "(장면 사진 수 × 비율, 올림, 최소 1장)"
        )
        help_text = bestcut_grouping_help(settings.gap_seconds, settings.split_distance)
        for widget in (*self._gap_widgets, self._split_checkbox, self._split_spin):
            widget.setToolTip(help_text + _DIFF_TOOLTIP)
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
            self._table.setItem(i, 3, QTableWidgetItem(self._difference_text(row)))
            choice = "선택" if row.selected else "제외"
            if row.manual_choice is not None:
                choice += " (직접)"
            self._table.setItem(i, 4, QTableWidgetItem(choice))
            self._table.setItem(i, 5, QTableWidgetItem(f"{row.total:.2f}"))
            sharpness_item = QTableWidgetItem(f"{row.sharpness:.1f}")
            sharpness_item.setToolTip(format_sharpness_region(row.sharpness_region))
            self._table.setItem(i, 6, sharpness_item)
            self._table.setItem(i, 7, QTableWidgetItem(f"{row.exposure:.2f}"))
            self._table.setItem(i, 8, QTableWidgetItem("" if row.face is None else f"{row.face:.2f}"))
            self._table.setItem(i, 9, QTableWidgetItem(row.reason))
        self._table.resizeRowsToContents()

    def _difference_text(self, row: ReportRow) -> str:
        """앞 사진과 차이 칸. 나누기를 켠 상태에서 이 차이 때문에 장면이 나뉜 자리는 ✂."""
        distance = self._distances.get(row.path)
        if distance is None:
            return ""
        split_distance = self._rows_grouping.split_distance if self._rows_grouping else None
        if split_distance is not None and distance > split_distance:
            return f"{distance} ✂"
        return str(distance)

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
        selected_count = sum(1 for r in self._rows if r.selected)
        manual = format_manual_choice_summary(self._rows)
        confirm = QMessageBox.question(
            self,
            "적용 확인",
            f"선택된 사진 {selected_count}장" + (f"({manual})" if manual else "")
            + f"을 {self._output_dir}(으)로 {verb}하시겠습니까?",
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
        elif self._photos is not None and self._current_grouping() != self._rows_grouping:
            # 적용하는 사이에 묶는 기준을 바꿨으면 이제 다시 묶는다.
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
