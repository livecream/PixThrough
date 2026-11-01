"""고객 셀렉 화면: 사진 폴더로 고객이 고를 수 있는 페이지(폴더+zip)를 만들고, 고객이 보낸 선택
결과(파일 또는 붙여넣은 목록)로 원본을 따로 복사한다(proof_gallery.py).

가져오기는 먼저 원본 폴더 사진과 맞춘 결과를 표로 보여주고(파일 작업 없음), "복사"를 눌러
확인해야 복사한다. 원본 폴더에는 아무것도 쓰지 않는다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
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
from gui_errors import overlap_warning, show_error
from gui_navigation import NextStep, NextStepBar
from gui_settings import (
    load_bool,
    load_float,
    load_folder,
    make_settings,
    pick_directory,
    save_bool,
    save_float,
    save_folder,
)
from gui_widgets import (
    EtaProgressBar,
    FLAGGED_ROW,
    GuidePanel,
    PathLabel,
    SummaryLabel,
    WarningLabel,
    compact,
    fit_text,
    make_primary,
    paint_row,
    status_row,
)
from gui_worker import FunctionWorker
from proof_gallery import (
    LONG_EDGE_START,
    ClientSelection,
    MatchedPick,
    ProofResult,
    build_proof,
    copy_selection,
    list_photos,
    match_selection,
    parse_selection,
    record_client_picks,
)
from xmp_rating import (
    ACTION_CREATED,
    ACTION_SKIPPED,
    ACTION_UPDATED,
    LABEL_PRESETS,
    XmpResult,
    default_run_dir,
    plan_xmp,
    undo_xmp,
    write_xmp,
)

INPUT_KEY = "proof/input_dir"
OUTPUT_KEY = "proof/output_dir"
COPY_KEY = "proof/copy_dir"
RECURSIVE_KEY = "proof/recursive"
LONG_EDGE_KEY = "proof/long_edge"
LIMIT_KEY = "proof/limit"
WATERMARK_KEY = "proof/watermark"
LABEL_KEY = "proof/xmp_label"

_COLUMNS = ("고객이 고른 사진", "원본", "메모")



def _folder_row(label: PathLabel, *buttons: QPushButton) -> QHBoxLayout:
    row = QHBoxLayout()
    row.addWidget(label, stretch=1)
    for button in buttons:
        row.addWidget(button)
    return row


class ProofTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._settings = make_settings()
        self._input_dir: Path | None = load_folder(self._settings, INPUT_KEY)
        self._output_dir: Path | None = load_folder(self._settings, OUTPUT_KEY)
        self._copy_dir: Path | None = load_folder(self._settings, COPY_KEY)
        self._worker: FunctionWorker | None = None
        self._task = ""  # 지금 도는 작업: "build" | "match" | "copy"
        self._made: ProofResult | None = None
        self._selection: ClientSelection | None = None
        self._matched: list[MatchedPick] = []
        self._missing: list[str] = []
        self._copied = False
        self.setAcceptDrops(True)

        # 사진 폴더(만들기·가져오기 공통)
        self._input_label = PathLabel("(폴더를 고르거나 끌어다 놓으세요)")
        self._input_label.set_path(self._input_dir)
        input_button = compact(QPushButton("선택…"))
        input_button.clicked.connect(self._choose_input_dir)
        self._recursive_checkbox = QCheckBox("하위 폴더까지 포함")
        self._recursive_checkbox.setChecked(load_bool(self._settings, RECURSIVE_KEY, False))
        self._recursive_checkbox.toggled.connect(self._on_recursive_changed)
        input_row = _folder_row(self._input_label, input_button)
        input_row.addWidget(self._recursive_checkbox)
        source_group = QGroupBox("사진 폴더 (원본 — 읽기만 합니다)")
        source_group.setLayout(input_row)

        # ① 페이지 만들기
        self._output_label = PathLabel()
        self._output_label.set_path(self._output_dir)
        output_button = compact(QPushButton("선택…"))
        output_button.clicked.connect(self._choose_output_dir)
        self._open_output_button = compact(QPushButton("열기"))
        self._open_output_button.clicked.connect(lambda: self._open_folder(self._output_dir))
        self._title_edit = QLineEdit()
        self._title_edit.setPlaceholderText("비우면 사진 폴더 이름")
        self._title_edit.setToolTip("<p>페이지 맨 위 제목이자, 만들어지는 폴더·zip 이름입니다.</p>")
        self._long_edge_spin = QSpinBox()
        self._long_edge_spin.setRange(200, 8000)
        self._long_edge_spin.setSingleStep(100)
        self._long_edge_spin.setSuffix(" px")
        self._long_edge_spin.setValue(int(load_float(self._settings, LONG_EDGE_KEY, LONG_EDGE_START, 200, 8000)))
        self._long_edge_spin.setToolTip(
            "<p>페이지에 넣을 사진의 긴 변 픽셀. 고르기에는 1600px 정도면 충분하고, 작을수록 "
            "zip이 가벼워집니다. 원본 화질을 그대로 넘기지 않는 효과도 있습니다.</p>"
        )
        self._long_edge_spin.valueChanged.connect(lambda v: save_float(self._settings, LONG_EDGE_KEY, v))
        self._limit_spin = QSpinBox()
        self._limit_spin.setRange(0, 9999)
        self._limit_spin.setSpecialValueText("제한 없음")
        self._limit_spin.setSuffix(" 장")
        self._limit_spin.setValue(int(load_float(self._settings, LIMIT_KEY, 0, 0, 9999)))
        self._limit_spin.setToolTip(
            "<p>고객이 고를 장 수(예: 보정 20장 계약). 페이지에 \"5 / 20장\"처럼 보이고 넘으면 "
            "빨갛게 바뀝니다 — 막지는 않습니다.</p>"
        )
        self._limit_spin.valueChanged.connect(lambda v: save_float(self._settings, LIMIT_KEY, v))
        self._watermark_edit = QLineEdit(str(self._settings.value(WATERMARK_KEY, "") or ""))
        self._watermark_edit.setPlaceholderText("비우면 넣지 않음 (예: 스튜디오 이름)")
        self._watermark_edit.setToolTip("<p>페이지 사진 가운데에 반투명 글자로 넣습니다. 원본은 그대로입니다.</p>")
        self._watermark_edit.textChanged.connect(lambda t: self._settings.setValue(WATERMARK_KEY, t))
        self._build_button = make_primary(QPushButton("페이지 만들기"))
        self._build_button.clicked.connect(self._run_build)

        make_form = QFormLayout()
        make_form.addRow("저장 폴더:", _folder_row(self._output_label, output_button, self._open_output_button))
        self._output_warning = WarningLabel()
        make_form.addRow(self._output_warning)
        make_form.addRow("제목:", self._title_edit)
        size_row = QHBoxLayout()
        size_row.addWidget(self._long_edge_spin)
        size_row.addWidget(QLabel("고를 장 수:"))
        size_row.addWidget(self._limit_spin)
        size_row.addStretch(1)
        make_form.addRow("사진 긴 변:", size_row)
        make_form.addRow("워터마크:", self._watermark_edit)
        build_row = QHBoxLayout()
        note = QLabel("촬영 정보(GPS 포함)는 빼고 저장")
        note.setEnabled(False)
        build_row.addWidget(note, stretch=1)
        build_row.addWidget(self._build_button)
        make_form.addRow(build_row)
        make_group = QGroupBox("① 고객에게 보낼 페이지 만들기")
        make_group.setLayout(make_form)

        # ② 고객 선택 가져오기
        open_result_button = compact(QPushButton("결과 파일 열기…"))
        open_result_button.setToolTip("<p>고객이 페이지에서 \"선택 결과 저장\"으로 보낸 .json 파일(또는 이름 목록 .txt)</p>")
        open_result_button.clicked.connect(self._open_result_file)
        paste_button = compact(QPushButton("목록 붙여넣기…"))
        paste_button.setToolTip("<p>고객이 \"목록 복사\"로 메신저에 보낸 글을 그대로 붙여 넣으세요. 줄마다 파일 이름 하나.</p>")
        paste_button.clicked.connect(self._paste_list)
        self._result_buttons = (open_result_button, paste_button)
        self._copy_label = PathLabel()
        self._copy_label.set_path(self._copy_dir)
        copy_dir_button = compact(QPushButton("선택…"))
        copy_dir_button.clicked.connect(self._choose_copy_dir)
        self._open_copy_button = compact(QPushButton("열기"))
        self._open_copy_button.clicked.connect(lambda: self._open_folder(self._copy_dir))
        self._copy_button = make_primary(QPushButton("복사"))
        self._copy_button.clicked.connect(self._run_copy)
        # 라이트룸 색 라벨(고객 선택 표시) — 원본 RAW 옆 .xmp에, 사진 파일은 그대로
        self._label_combo = QComboBox()
        self._label_combo.setEditable(True)
        self._label_combo.addItems(LABEL_PRESETS)
        self._label_combo.setCurrentText(str(self._settings.value(LABEL_KEY, LABEL_PRESETS[0]) or LABEL_PRESETS[0]))
        self._label_combo.setMinimumContentsLength(8)
        self._label_combo.setToolTip(
            "<p>고객이 고른 사진의 RAW 옆 .xmp에 이 색 라벨을 씁니다(별점은 그대로). 라이트룸은 라벨 "
            "글자가 지금 쓰는 색상 레이블 세트 이름과 같아야 색으로 보여줍니다 — 영문 라이트룸은 "
            "Green·Red 등, 한국어 라이트룸에서 색이 안 보이면 라이트룸의 메타데이터 › 색상 레이블 세트 › "
            "편집에 나오는 이름을 그대로 적으세요.</p>"
        )
        self._label_combo.currentTextChanged.connect(lambda text: self._settings.setValue(LABEL_KEY, text.strip()))
        self._label_button = compact(QPushButton("라벨 쓰기"))
        self._label_button.clicked.connect(self._run_label)
        self._undo_label_button = compact(QPushButton("↶ 라벨 되돌리기"))
        self._undo_label_button.setToolTip(
            "<p>방금 쓴 라벨을 되돌립니다: 새로 만든 .xmp는 지우고, 고친 .xmp는 백업으로. 그 뒤로 "
            "라이트룸 등이 다시 쓴 파일은 그대로 둡니다.</p>"
        )
        self._undo_label_button.clicked.connect(self._undo_label)
        self._last_label: list[XmpResult] | None = None
        self._label_run_dir: Path | None = None

        import_form = QFormLayout()
        result_row = QHBoxLayout()
        result_row.addWidget(open_result_button)
        result_row.addWidget(paste_button)
        result_row.addStretch(1)
        import_form.addRow("고객 선택:", result_row)
        import_form.addRow("복사할 폴더:", _folder_row(self._copy_label, copy_dir_button, self._open_copy_button))
        self._copy_warning = WarningLabel()
        import_form.addRow(self._copy_warning)
        copy_row = QHBoxLayout()
        copy_note = QLabel("원본(+같은 이름 RAW)을 복사 · 원본은 그대로")
        copy_note.setEnabled(False)
        copy_row.addWidget(copy_note, stretch=1)
        copy_row.addWidget(self._copy_button)
        import_form.addRow(copy_row)
        label_row = QHBoxLayout()
        label_row.addWidget(QLabel("색 라벨"))
        label_row.addWidget(self._label_combo)
        label_row.addStretch(1)
        label_row.addWidget(self._undo_label_button)
        label_row.addWidget(self._label_button)
        import_form.addRow("라이트룸:", label_row)
        import_group = QGroupBox("② 고객이 고른 사진 가져오기")
        import_group.setLayout(import_form)

        # 진행·결과
        self._cancel_button = compact(QPushButton("취소"))
        self._cancel_button.setEnabled(False)
        self._cancel_button.clicked.connect(self._cancel)
        self._progress_bar = EtaProgressBar()
        self._status_label = QLabel("")
        self._summary = SummaryLabel()
        self._next_steps = NextStepBar()
        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._guide = GuidePanel(
            "고객 셀렉 순서",
            [
                "사진 폴더 고르기 (끌어다 놓아도 됩니다)",
                "① 페이지 만들기 — 저장 폴더에 페이지 폴더와 zip이 생깁니다",
                "zip을 구글 드라이브·메일 등으로 고객에게 보내기 — 고객은 압축을 풀고 index.html을 열어 고릅니다",
                "② 고객이 보낸 결과 파일을 열거나 목록을 붙여넣기 — 원본과 맞춘 결과를 확인",
                "복사 — 고른 사진 원본을 따로 폴더에",
            ],
        )
        self._results = QStackedWidget()
        self._results.addWidget(self._guide)
        self._results.addWidget(self._table)

        middle = QHBoxLayout()
        middle.addWidget(make_group, stretch=1)
        middle.addWidget(import_group, stretch=1)
        progress_row = QHBoxLayout()
        progress_row.addLayout(status_row(self._progress_bar, self._status_label), stretch=1)
        progress_row.addWidget(self._cancel_button)

        layout = QVBoxLayout()
        layout.addWidget(source_group)
        layout.addLayout(middle)
        layout.addLayout(progress_row)
        layout.addWidget(self._summary)
        layout.addWidget(self._next_steps)
        layout.addWidget(self._results, stretch=1)
        self.setLayout(layout)
        self._refresh()

    @property
    def is_busy(self) -> bool:
        return self._worker is not None

    # --- 폴더 ---------------------------------------------------------------------
    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt override signature
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt override signature
        folders = extract_dropped_folders(event.mimeData())
        if folders and not self.is_busy:
            self._set_input_dir(folders[0])
        event.acceptProposedAction()

    def receive_folders(self, folders: list[Path], replace: bool = True) -> None:
        """다른 화면의 "다음 단계"로 넘어올 때(첫 폴더를 사진 폴더로)."""
        if folders and not self.is_busy:
            self._set_input_dir(folders[0])

    def _choose_input_dir(self) -> None:
        directory = pick_directory(self, "사진 폴더 선택")
        if directory:
            self._set_input_dir(Path(directory))

    def _set_input_dir(self, folder: Path) -> None:
        self._input_dir = folder
        self._input_label.set_path(folder)
        save_folder(self._settings, INPUT_KEY, folder)
        self._clear_import()

    def _on_recursive_changed(self, checked: bool) -> None:
        save_bool(self._settings, RECURSIVE_KEY, checked)
        self._clear_import()

    def _choose_output_dir(self) -> None:
        directory = pick_directory(self, "페이지를 만들 폴더 선택")
        if directory:
            self._output_dir = Path(directory)
            self._output_label.set_path(self._output_dir)
            save_folder(self._settings, OUTPUT_KEY, self._output_dir)
            self._refresh()

    def _choose_copy_dir(self) -> None:
        directory = pick_directory(self, "고객이 고른 사진을 복사할 폴더 선택")
        if directory:
            self._copy_dir = Path(directory)
            self._copy_label.set_path(self._copy_dir)
            save_folder(self._settings, COPY_KEY, self._copy_dir)
            self._refresh()

    def _open_folder(self, folder: Path | None) -> None:
        if folder is not None and not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
            QMessageBox.warning(self, "폴더 열기 실패", f"폴더를 열지 못했습니다: {folder}")

    def _clear_import(self) -> None:
        """사진 폴더가 바뀌면 맞춘 결과가 맞지 않아 지운다(고객 선택은 다시 열어야 함)."""
        self._selection = None
        self._matched, self._missing = [], []
        self._copied = False
        self._next_steps.clear()
        self._table.setRowCount(0)
        self._summary.setPlainText("")
        self._refresh()

    # --- ① 페이지 만들기 ------------------------------------------------------------
    def _run_build(self) -> None:
        if self.is_busy:
            return
        if self._input_dir is None or self._output_dir is None:
            QMessageBox.warning(self, "폴더 필요", "사진 폴더와 저장 폴더를 먼저 고르세요.")
            return
        folder, output = self._input_dir, self._output_dir
        title = self._title_edit.text().strip() or folder.name
        args = dict(
            long_edge=self._long_edge_spin.value(),
            watermark=self._watermark_edit.text().strip(),
            max_picks=self._limit_spin.value(),
            recursive=self._recursive_checkbox.isChecked(),
        )
        self._start("build", "페이지 만드는 중...", lambda progress: build_proof(
            folder, output, title, on_progress=progress, **args
        ))

    def _on_build_done(self, result: ProofResult) -> None:
        self._made = result
        lines = [
            f"페이지를 만들었습니다: 사진 {len(result.photos)}장",
            f"폴더: {result.package_dir}",
            f"zip: {result.zip_path}",
            "zip을 고객에게 보내세요. 고객은 압축을 풀고 index.html을 PC 브라우저로 열어 고릅니다"
            " (휴대폰에서 보려면 폴더를 웹에 올려야 합니다).",
        ]
        if result.failed:
            lines.append(f"열지 못해 뺀 사진 {len(result.failed)}장: "
                         + ", ".join(path.name for path, _ in result.failed[:5])
                         + (" …" if len(result.failed) > 5 else ""))
        self._summary.setPlainText("\n".join(lines))
        self._status_label.setText("페이지 완료")

    # --- ② 고객 선택 가져오기 -------------------------------------------------------
    def _open_result_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "고객이 보낸 결과 파일 열기", "", "선택 결과 (*.json *.txt);;모든 파일 (*)"
        )
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError) as e:
            QMessageBox.warning(self, "파일을 읽지 못했습니다", str(e))
            return
        self._load_selection(text)

    def _paste_list(self) -> None:
        text, ok = QInputDialog.getMultiLineText(
            self, "목록 붙여넣기", "고객이 보낸 목록(줄마다 파일 이름 하나, \"이름 - 메모\"도 됨):"
        )
        if ok and text.strip():
            self._load_selection(text)

    def _load_selection(self, text: str) -> None:
        if self.is_busy:
            return
        if self._input_dir is None:
            QMessageBox.warning(self, "폴더 필요", "고객에게 보낸 사진의 원본 폴더를 먼저 고르세요.")
            return
        selection = parse_selection(text)
        if not selection.ids:
            QMessageBox.warning(self, "고른 사진 없음", "고른 사진을 찾지 못했습니다. 결과 파일이나 목록을 확인해 주세요.")
            return
        folder, recursive = self._input_dir, self._recursive_checkbox.isChecked()
        self._selection = selection

        def match(progress) -> tuple[list[MatchedPick], list[str]]:
            items = list_photos(folder, recursive)
            matched, missing = match_selection(selection, folder, recursive, items=items)
            record_client_picks(matched, [path for _, path in items])  # 베스트컷 화면 "고객 선택" 표시
            return matched, missing

        self._start("match", "원본과 맞추는 중...", match)

    def _on_match_done(self, value: tuple[list[MatchedPick], list[str]]) -> None:
        self._matched, self._missing = value
        self._copied = False
        self._populate()
        selection = self._selection
        lines = [f"고객이 고른 {len(selection.ids)}장 중 원본을 찾은 사진 {len(self._matched)}장"
                 + (f", 못 찾음 {len(self._missing)}개(빨간 줄)" if self._missing else "")]
        if selection.title:
            lines.append(f"페이지 제목: {selection.title}")
        if selection.memo:
            lines.append(f"고객 메모: {selection.memo}")
        if self._missing:
            lines.append("못 찾은 사진은 사진 폴더·하위 폴더 포함 설정이 페이지를 만들 때와 같은지 확인하세요.")
        self._summary.setPlainText("\n".join(lines))
        self._status_label.setText("맞추기 완료 — 확인하고 복사하세요")

    def _populate(self) -> None:
        rows: list[tuple[str, str, str, bool]] = []
        base = self._input_dir
        for pick in self._matched:
            try:
                shown = pick.source.relative_to(base).as_posix() if base else pick.source.name
            except ValueError:
                shown = pick.source.name
            rows.append((pick.id, shown, pick.note, True))
        notes = self._selection.notes if self._selection else {}
        for name in self._missing:
            rows.append((name, "못 찾음", notes.get(name, ""), False))
        self._table.setRowCount(len(rows))
        for i, (wanted, source, note, found) in enumerate(rows):
            for column, text in enumerate((wanted, source, note)):
                self._table.setItem(i, column, QTableWidgetItem(text))
            paint_row(self._table, i, None if found else FLAGGED_ROW)

    def _run_copy(self) -> None:
        if self.is_busy or not self._matched or self._copy_dir is None or self._input_dir is None:
            return
        missing = f"\n못 찾은 {len(self._missing)}개는 건너뜁니다." if self._missing else ""
        confirm = QMessageBox.question(
            self,
            "고객이 고른 사진 복사",
            f"{len(self._matched)}장의 원본(같은 이름 RAW는 RAW/ 폴더로)을 다음 폴더에 복사합니다:\n"
            f"{self._copy_dir}\n\n원본은 그대로 두고, 같은 이름이 있으면 덮어쓰지 않고 번호를 붙입니다."
            f"{missing}\n계속하시겠습니까?",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        matched, copy_dir, folder = list(self._matched), self._copy_dir, self._input_dir
        self._start("copy", "복사 중...", lambda progress: copy_selection(matched, copy_dir, folder, progress))

    def _on_copy_done(self, value) -> None:
        results, report = value
        self._copied = True
        self._summary.setPlainText(
            f"복사 완료: {len(results)}장 → {self._copy_dir}\n목록(메모 포함): {report.name}"
            + (f"\n고객 메모: {self._selection.memo}" if self._selection and self._selection.memo else "")
        )
        self._status_label.setText("복사 완료")
        if self._copy_dir is not None:
            folders = (self._copy_dir,)
            self._next_steps.set_steps([NextStep("resize", folders), NextStep("rename", folders)])

    # --- 라이트룸 색 라벨 --------------------------------------------------------------
    def _run_label(self) -> None:
        if self.is_busy or not self._matched:
            return
        label = self._label_combo.currentText().strip()
        if not label:
            QMessageBox.warning(self, "라벨 이름 필요", "색 라벨 이름을 적어 주세요(예: Green).")
            return
        plan = plan_xmp([], [], [pick.source for pick in self._matched])
        with_raw = [row for row in plan if row.xmp is not None]
        if not with_raw:
            QMessageBox.information(
                self, "라이트룸 색 라벨",
                "고객이 고른 사진 중 RAW가 있는 사진이 없습니다. 라이트룸은 RAW 옆의 .xmp만 읽어서 "
                "JPG만 있는 사진에는 라벨을 쓸 수 없습니다.",
            )
            return
        existing = sum(1 for row in with_raw if row.existing)
        lines = [
            f"고객이 고른 {len(with_raw)}장의 원본 RAW 옆 .xmp에 색 라벨 \"{label}\"을 씁니다"
            "(별점·보정 기록과 RAW·JPG 파일은 그대로).",
            f"새로 만들기 {len(with_raw) - existing}개 · 기존 .xmp 고치기 {existing}개(라벨 칸만, 고치기 전 파일은 백업)",
        ]
        if len(plan) > len(with_raw):
            lines.append(f"RAW가 없어 건너뜀 {len(plan) - len(with_raw)}장 (JPG만 있는 사진)")
        if QMessageBox.question(self, "라이트룸 색 라벨 쓰기", "\n".join(lines) + "\n\n계속하시겠습니까?") \
                != QMessageBox.StandardButton.Yes:
            return
        run_dir = default_run_dir(app_log.app_data_dir())
        self._label_run_dir = run_dir
        self._start("label", "라이트룸 라벨 쓰는 중...", lambda progress: write_xmp(plan, 0, run_dir, label=label))

    def _on_label_done(self, results: list[XmpResult]) -> None:
        created = sum(1 for r in results if r.action == ACTION_CREATED)
        updated = sum(1 for r in results if r.action == ACTION_UPDATED)
        skipped = [r for r in results if r.action == ACTION_SKIPPED]
        if created or updated:
            self._last_label = results
        self._status_label.setText(f"라이트룸 라벨: 만듦 {created} · 고침 {updated} · 건너뜀 {len(skipped)}")
        self._summary.setPlainText(
            f"라이트룸 색 라벨: .xmp 새로 만듦 {created}개 · 고침 {updated}개 · 건너뜀 {len(skipped)}장\n"
            f"백업·처리 기록: {self._label_run_dir}\n"
            "라이트룸에 이미 가져온 사진은 메타데이터 → 파일에서 메타데이터 읽기를 해야 반영됩니다."
        )
        unsafe = [r for r in skipped if r.plan.xmp is not None]
        if unsafe:
            listing = "\n".join(f"- {r.plan.xmp.name}: {r.reason}" for r in unsafe[:10])
            QMessageBox.warning(self, "일부 .xmp는 건드리지 않음", f"{len(unsafe)}개는 그대로 두었습니다:\n{listing}")

    def _undo_label(self) -> None:
        if self.is_busy or not self._last_label:
            return
        results = self._last_label
        count = sum(1 for r in results if r.action != ACTION_SKIPPED and (r.action == ACTION_CREATED or r.backup))
        if QMessageBox.question(
            self, "라벨 되돌리기",
            f"방금 쓴 라이트룸 라벨 {count}개를 되돌립니다: 새로 만든 .xmp는 지우고, 고친 .xmp는 백업으로 "
            "되돌립니다. 그 뒤로 내용이 바뀐 파일은 그대로 둡니다. 계속하시겠습니까?",
        ) != QMessageBox.StandardButton.Yes:
            return
        restored, skipped = undo_xmp(results)
        self._last_label = None
        text = f"라벨 {restored}개를 되돌렸습니다"
        if skipped:
            text += f" · {len(skipped)}개는 그 뒤로 바뀌어 그대로 둠"
        self._status_label.setText(text)
        self._summary.setPlainText(text)
        self._refresh()

    # --- 공통 ---------------------------------------------------------------------
    def _start(self, task: str, status: str, fn: Callable) -> None:
        self._task = task
        self._worker = FunctionWorker(fn, "고객 셀렉 작업")
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_done)
        self._worker.failed.connect(self._on_error)
        self._worker.cancelled.connect(self._on_cancelled)
        self._progress_bar.setValue(0)
        self._status_label.setText(status)
        self._worker.start()
        self._refresh()

    def _finish(self) -> str:
        task, self._task, self._worker = self._task, "", None
        self._refresh()
        return task

    def _on_done(self, value) -> None:
        task = self._finish()
        if task == "build":
            self._on_build_done(value)
        if task in ("match", "label"):
            self._progress_bar.setMaximum(1)  # 맞추기·라벨 쓰기는 진행률을 따로 알리지 않는다
            self._progress_bar.setValue(1)
        if task == "match":
            self._on_match_done(value)
        elif task == "copy":
            self._on_copy_done(value)
        elif task == "label":
            self._on_label_done(value)
        self._refresh()

    def _on_progress(self, done: int, total: int) -> None:
        self._progress_bar.setMaximum(total)
        self._progress_bar.setValue(done)

    def _cancel(self) -> None:
        if self._worker is not None:
            self._worker.request_cancel()
        self._cancel_button.setEnabled(False)
        self._status_label.setText("멈추는 중...")

    def _on_cancelled(self) -> None:
        task = self._finish()
        if task == "build":
            self._summary.setPlainText(
                "페이지 만들기를 멈췄습니다. 만들다 만 페이지 폴더가 저장 폴더에 남아 있으니 필요 없으면 지우세요."
            )
        elif task == "copy":
            self._copied = True  # 일부는 이미 복사됐을 수 있어, 다시 하려면 결과를 다시 열기
            self._summary.setPlainText("복사를 멈췄습니다. 그때까지 복사한 사진은 복사할 폴더에 남아 있습니다.")
        self._status_label.setText("취소했습니다")
        self._refresh()

    def _on_error(self, message: str) -> None:
        task = self._finish()
        if task == "copy":
            self._copied = True
        self._status_label.setText("")
        if task == "copy":
            message = "복사하지 못한 파일이 있습니다. 복사된 것은 복사할 폴더에 남아 있습니다.\n\n" + message
        show_error(self, "오류", message)
        self._refresh()

    def _refresh(self) -> None:
        sources = [self._input_dir] if self._input_dir else []
        self._output_warning.set_warning(overlap_warning(sources, self._output_dir, "저장 폴더"))
        self._copy_warning.set_warning(overlap_warning(sources, self._copy_dir, "복사할 폴더"))
        busy = self.is_busy
        self._cancel_button.setEnabled(busy and self._task != "match")
        self._build_button.setEnabled(not busy)
        for button in self._result_buttons:
            button.setEnabled(not busy)
        count = 0 if self._copied else len(self._matched)
        self._copy_button.setText(f"복사 ({count}장)" if count else "복사")
        fit_text(self._copy_button)
        self._copy_button.setEnabled(bool(count) and not busy and self._copy_dir is not None)
        self._label_button.setText(f"라벨 쓰기 ({len(self._matched)}장)" if self._matched else "라벨 쓰기")
        self._label_button.setEnabled(bool(self._matched) and not busy)
        self._undo_label_button.setVisible(bool(self._last_label))
        self._undo_label_button.setEnabled(not busy)
        self._open_output_button.setEnabled(self._output_dir is not None)
        self._open_copy_button.setEnabled(self._copy_dir is not None)
        has_table = self._task != "match" and self._table.rowCount() > 0
        self._results.setCurrentWidget(self._table if has_table else self._guide)
        self._guide.set_done(
            [
                self._input_dir is not None,
                self._made is not None,
                self._made is not None,
                bool(self._matched),
                self._copied,
            ]
        )
