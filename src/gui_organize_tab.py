"""M11: 폴더 정리 탭. RAW/영상 분리, 짝 안 맞는 파일 찾기, 촬영 날짜별 정리,
빈 폴더 정리, 중복 파일 정리를 폴더 하나 선택 후 수행한다.

버튼은 하는 일의 위험도대로 세 칸에 나눠 둔다: 살펴보기(파일을 바꾸지 않음) /
정리(파일 이동, 되돌리기 가능) / 삭제(확인 후 지움). 정리·삭제 버튼은 바로 옮기거나 지우지
않고, 대상을 표로 먼저 보여준 뒤 아래 실행 버튼("RAW 12개 이동" 등)을 눌러야 실행한다.
훑기(EXIF 읽기·해시 계산)와 실행은 작업 스레드에서 돌아 화면이 멈추지 않고, 오래 걸리는
훑기는 취소할 수 있다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui_dnd import extract_dropped_folders
from gui_errors import show_error
from gui_format import format_bytes, format_folder_summary
from gui_navigation import NextStep, NextStepBar
from gui_preview_pane import PreviewPane
from gui_reveal import add_file_actions
from gui_settings import load_folder, make_settings, pick_directory, save_folder
from gui_widgets import (
    EtaProgressBar,
    FLAGGED_ROW,
    KEPT_ROW,
    GuidePanel,
    PathLabel,
    SummaryLabel,
    compact,
    fit_text,
    make_danger,
    make_primary,
    paint_row,
    status_row,
)
from gui_worker import FunctionWorker
from organize import (
    DuplicateGroup,
    MovePlanRow,
    apply_move_plan,
    build_capture_date_plan,
    build_move_by_extension_plan,
    build_undo_plan,
    delete_duplicate_files,
    find_duplicate_files,
    find_empty_folders,
    find_raw_jpg_mismatches,
    remove_empty_folders,
    summarize_folder,
)
from rename_by_time import VIDEO_EXTENSIONS
from scanner import RAW_EXTENSIONS

_PATH_ROLE = Qt.ItemDataRole.UserRole + 1
_SKIPPED_ROW = QColor(128, 128, 128, 60)

# 정리 버튼 → (이동 이름, 하위 폴더 이름 또는 None=날짜별, 기록 이름)
_MOVES = {
    "raw": ("RAW", "RAW", "move_raw"),
    "video": ("영상", "영상", "move_video"),
    "date": ("날짜별", None, "organize_by_date"),
}


def _section(title: str, actions: list[tuple[QPushButton, str]]) -> QGroupBox:
    """제목 상자 안에 버튼과 그 아래 한 줄 설명을 차례로 쌓는다."""
    layout = QVBoxLayout()
    for button, description in actions:
        layout.addWidget(button)
        label = QLabel(description)
        label.setWordWrap(True)
        palette = label.palette()  # 설명은 흐린 글자색으로
        palette.setColor(QPalette.ColorRole.WindowText, palette.color(QPalette.ColorRole.PlaceholderText))
        label.setPalette(palette)
        layout.addWidget(label)
        layout.addSpacing(6)
    layout.addStretch()
    group = QGroupBox(title)
    group.setLayout(layout)
    return group


class OrganizeTab(QWidget):
    def __init__(self) -> None:
        super().__init__()

        self._folder: Path | None = None
        # 직전 정리 작업 하나만 기억해 "실행 취소"로 되돌린다. 앱을 껐다 켜면
        # 사라짐(세션 한정) — organize_log_*.jsonl에는 남아있어 수동 확인 가능.
        self._last_operation: dict | None = None
        # 표로 보여준 뒤 실행 버튼을 기다리는 작업: ("move", 종류, rows) / ("empty", folders) / ("dup", groups)
        self._pending: tuple | None = None
        self._worker: FunctionWorker | None = None
        self._task = ""
        self.setAcceptDrops(True)
        self._settings = make_settings()

        self._folder_label = PathLabel("(선택 안 됨 — 폴더를 이 탭에 끌어다 놓아도 됩니다)")
        folder_button = compact(QPushButton("선택…"))
        folder_button.clicked.connect(self._choose_folder)

        summary_button = QPushButton("폴더 정보 보기")
        summary_button.clicked.connect(self._show_folder_summary)
        raw_button = QPushButton("RAW 폴더로 이동…")
        raw_button.clicked.connect(lambda: self._plan_move("raw"))
        video_button = QPushButton("영상 폴더로 이동…")
        video_button.clicked.connect(lambda: self._plan_move("video"))
        mismatch_button = QPushButton("RAW-JPG 짝 안 맞는 파일 찾기")
        mismatch_button.clicked.connect(self._find_mismatches)
        date_button = QPushButton("촬영 날짜별로 정리…")
        date_button.clicked.connect(lambda: self._plan_move("date"))
        empty_button = make_danger(QPushButton("빈 폴더 정리…"))
        empty_button.clicked.connect(self._find_empty_folders)
        find_duplicate_button = make_danger(QPushButton("중복 파일 찾아서 정리…"))
        find_duplicate_button.clicked.connect(self._find_duplicates)
        self._action_buttons = (
            folder_button, summary_button, raw_button, video_button, mismatch_button,
            date_button, empty_button, find_duplicate_button,
        )

        self._run_button = make_primary(QPushButton("실행"))
        self._run_button.clicked.connect(self._run_pending)
        self._undo_button = compact(QPushButton("↶ 방금 작업 되돌리기"))
        self._undo_button.setToolTip(
            "<p>마지막 이동(RAW·영상·날짜별 정리)이나 빈 폴더 정리를 되돌립니다. 중복 파일 "
            "삭제는 되돌릴 수 없습니다. 앱을 끄면 기억이 사라지지만, 폴더 안 "
            "organize_log_*.jsonl에 기록이 남습니다.</p>"
        )
        self._undo_button.clicked.connect(self._undo_last_operation)
        self._cancel_button = compact(QPushButton("취소"))
        self._cancel_button.clicked.connect(self._cancel)
        self._progress_bar = EtaProgressBar()
        self._status_label = QLabel("")
        self._summary = SummaryLabel()
        self._next_steps = NextStepBar()

        self._table = QTableWidget(0, 0)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.verticalHeader().setVisible(False)
        self._table.itemSelectionChanged.connect(self._on_row_selected)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)
        self._preview_pane = PreviewPane()
        table_box = QHBoxLayout()
        table_box.setContentsMargins(0, 0, 0, 0)
        table_box.addWidget(self._table, stretch=1)
        table_box.addWidget(self._preview_pane)
        table_widget = QWidget()
        table_widget.setLayout(table_box)
        self._guide = GuidePanel(
            "폴더 정리 순서",
            [
                "정리할 폴더 고르기 (끌어다 놓아도 됩니다)",
                "위 버튼 하나 누르기 — 대상 파일을 표로 먼저 보여줍니다 (아직 그대로)",
                "표를 확인하고 아래 실행 버튼 누르기 (이동은 \"↶ 방금 작업 되돌리기\" 가능)",
            ],
        )
        self._results = QStackedWidget()
        self._results.addWidget(self._guide)
        self._results.addWidget(table_widget)

        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("정리할 폴더:"))
        folder_row.addWidget(self._folder_label, stretch=1)
        folder_row.addWidget(folder_button)

        sections = QHBoxLayout()
        sections.addWidget(
            _section(
                "살펴보기 — 파일을 바꾸지 않음",
                [
                    (summary_button, "하위 폴더까지 파일 수·용량을 종류(사진·RAW·영상 등)별로 셉니다."),
                    (mismatch_button, "같은 이름의 짝이 없는 RAW나 JPG를 알려줍니다. 옮기거나 지우지 않습니다."),
                ],
            ),
            stretch=1,
        )
        sections.addWidget(
            _section(
                "정리 — 표로 확인 후 이동 (되돌리기 가능)",
                [
                    (raw_button, "맨 위 폴더의 RAW 파일을 RAW/ 하위 폴더로 옮깁니다."),
                    (video_button, "맨 위 폴더의 영상 파일을 영상/ 하위 폴더로 옮깁니다."),
                    (date_button, "맨 위 폴더의 사진·영상을 촬영 날짜(YYYY-MM-DD) 폴더로 옮깁니다."),
                ],
            ),
            stretch=1,
        )
        sections.addWidget(
            _section(
                "삭제 — 표로 확인 후 지움",
                [
                    (empty_button, "파일이 하나도 없는 폴더를 하위까지 찾아 지웁니다. 되돌리기 가능."),
                    (
                        find_duplicate_button,
                        "내용이 완전히 같은 파일을 하위 폴더까지 찾아, 그룹마다 1개만 남기고 지웁니다. "
                        "지운 파일은 되돌릴 수 없습니다.",
                    ),
                ],
            ),
            stretch=1,
        )

        progress_row = QHBoxLayout()
        progress_row.addLayout(status_row(self._progress_bar, self._status_label), stretch=1)
        progress_row.addWidget(self._cancel_button)
        action_row = QHBoxLayout()
        action_row.addWidget(self._summary, stretch=1)
        action_row.addWidget(self._undo_button, alignment=Qt.AlignmentFlag.AlignBottom)
        action_row.addWidget(self._run_button, alignment=Qt.AlignmentFlag.AlignBottom)

        layout = QVBoxLayout()
        layout.addLayout(folder_row)
        layout.addLayout(sections)
        layout.addLayout(progress_row)
        layout.addLayout(action_row)
        layout.addWidget(self._next_steps)
        layout.addWidget(self._results, stretch=1)

        restored_folder = load_folder(self._settings, "organize/folder")
        if restored_folder is not None:
            self._folder = restored_folder
            self._folder_label.set_path(restored_folder)

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
            self._set_folder(folders[0])
        event.acceptProposedAction()

    def _choose_folder(self) -> None:
        directory = pick_directory(self, "정리할 폴더 선택")
        if directory:
            self._set_folder(Path(directory))

    def _set_folder(self, folder: Path) -> None:
        self._folder = folder
        self._folder_label.set_path(folder)
        self._last_operation = None
        self._show_nothing()
        save_folder(self._settings, "organize/folder", folder)

    def receive_folders(self, folders: list[Path], replace: bool = True) -> None:
        """다른 화면의 "다음 단계"로 넘어올 때(폴더 하나만 정리하므로 첫 폴더)."""
        if folders:
            self._set_folder(folders[0])

    def _offer_next_steps(self) -> None:
        if self._folder is not None:
            folders = (self._folder,)
            self._next_steps.set_steps([NextStep("rename", folders), NextStep("selection", folders)])

    def _require_folder(self) -> Path | None:
        if self.is_busy:
            return None
        if self._folder is None or not self._folder.is_dir():
            QMessageBox.warning(self, "폴더 없음", "먼저 정리할 폴더를 선택하세요.")
            return None
        return self._folder

    def _relative(self, path: Path) -> str:
        try:
            return path.relative_to(self._folder).as_posix() if self._folder else str(path)
        except ValueError:
            return str(path)

    # --- 결과 표 ------------------------------------------------------------------
    def _show_nothing(self, summary: str = "") -> None:
        self._pending = None
        self._next_steps.clear()
        self._table.setRowCount(0)
        self._summary.setPlainText(summary)
        self._preview_pane.set_path(None)
        self._refresh()

    def _fill_table(
        self, headers: tuple[str, ...], rows: list[tuple[Path | None, tuple[str, ...], QColor | None]]
    ) -> None:
        """rows: (미리 볼 파일, 칸 글자들, 줄 색)."""
        self._table.setRowCount(0)
        self._table.setColumnCount(len(headers))
        self._table.setHorizontalHeaderLabels(headers)
        self._table.setRowCount(len(rows))
        for i, (path, values, color) in enumerate(rows):
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setData(_PATH_ROLE, path)
                if column == 0 and path is not None:
                    item.setToolTip(str(path))
                self._table.setItem(i, column, item)
            if color is not None:
                paint_row(self._table, i, color)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, len(headers)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        if rows:
            self._table.selectRow(0)
        self._refresh()

    def _on_row_selected(self) -> None:
        items = self._table.selectedItems()
        path = items[0].data(_PATH_ROLE) if items else None
        self._preview_pane.set_path(path if isinstance(path, Path) and path.is_file() else None)

    def _show_context_menu(self, pos) -> None:
        item = self._table.itemAt(pos)
        path = item.data(_PATH_ROLE) if item is not None else None
        if not isinstance(path, Path) or not path.exists():
            return
        menu = QMenu(self)
        add_file_actions(menu, self, path)
        menu.exec(self._table.viewport().mapToGlobal(pos))

    # --- 살펴보기 -------------------------------------------------------------------
    def _show_folder_summary(self) -> None:
        folder = self._require_folder()
        if folder is None:
            return
        self._start("summary", "세는 중...", lambda progress: summarize_folder(folder, VIDEO_EXTENSIONS))

    def _find_mismatches(self) -> None:
        folder = self._require_folder()
        if folder is None:
            return
        self._start("mismatch", "찾는 중...", lambda progress: find_raw_jpg_mismatches(folder))

    def _show_mismatches(self, messages: list[str]) -> None:
        if not messages:
            self._show_nothing("짝이 안 맞는 파일이 없습니다.")
            return
        rows = []
        for message in messages:
            problem, _, name = message.partition(": ")
            rows.append((self._folder / name, (name, problem), FLAGGED_ROW))
        self._pending = None
        self._summary.setPlainText(f"짝 안 맞는 파일 {len(messages)}개 (옮기거나 지우지 않았습니다)")
        self._fill_table(("파일", "문제"), rows)

    # --- 정리(이동) ---------------------------------------------------------------
    def _plan_move(self, kind: str) -> None:
        folder = self._require_folder()
        if folder is None:
            return
        _, subfolder, _ = _MOVES[kind]
        if subfolder is None:
            fn: Callable = lambda progress: build_capture_date_plan(folder, VIDEO_EXTENSIONS, progress)
        else:
            extensions = RAW_EXTENSIONS if kind == "raw" else VIDEO_EXTENSIONS
            fn = lambda progress: build_move_by_extension_plan(folder, extensions, subfolder)  # noqa: E731
        self._start("plan_" + kind, "옮길 파일 찾는 중...", fn)

    def _show_move_plan(self, kind: str, rows: list[MovePlanRow]) -> None:
        label, _, _ = _MOVES[kind]
        if not rows:
            self._show_nothing(f"{label} 정리로 옮길 파일이 없습니다.")
            return
        table_rows = []
        conflicts = 0
        for row in rows:
            exists = row.new_path.exists()
            conflicts += exists
            status = "이미 같은 이름이 있어 건너뜀" if exists else ""
            table_rows.append(
                (row.old_path, (row.old_path.name, self._relative(row.new_path.parent) + "/", status),
                 _SKIPPED_ROW if exists else None)
            )
        movable = len(rows) - conflicts
        destinations = sorted({self._relative(r.new_path.parent) for r in rows})
        text = f"{len(rows)}개 파일을 {', '.join(destinations[:6])}{' …' if len(destinations) > 6 else ''} 폴더로 옮길 예정"
        if conflicts:
            text += f" · {conflicts}개는 옮길 자리에 같은 이름이 있어 건너뜀(덮어쓰지 않음)"
        text += "\n표를 확인하고 아래 버튼을 눌러야 옮깁니다."
        self._summary.setPlainText(text)
        self._pending = ("move", kind, rows, movable)
        self._fill_table(("파일", "옮길 곳", "비고"), table_rows)

    # --- 삭제 ---------------------------------------------------------------------
    def _find_empty_folders(self) -> None:
        folder = self._require_folder()
        if folder is None:
            return
        self._start("empty", "빈 폴더 찾는 중...", lambda progress: find_empty_folders(folder))

    def _show_empty_folders(self, folders: list[Path]) -> None:
        if not folders:
            self._show_nothing("빈 폴더가 없습니다.")
            return
        self._summary.setPlainText(
            f"파일이 하나도 없는 폴더 {len(folders)}개 · 아래 버튼을 눌러야 지웁니다(되돌리기 가능)."
        )
        self._pending = ("empty", folders)
        self._fill_table(("빈 폴더",), [(None, (self._relative(p) + "/",), None) for p in folders])

    def _find_duplicates(self) -> None:
        folder = self._require_folder()
        if folder is None:
            return
        self._start("dup", "같은 파일 찾는 중(내용 비교)...", lambda progress: find_duplicate_files(folder, progress))

    def _show_duplicates(self, groups: list[DuplicateGroup]) -> None:
        if not groups:
            self._show_nothing("중복 파일이 없습니다.")
            return
        rows = []
        total = 0
        saved = 0
        for number, group in enumerate(groups, start=1):
            size = group.keep.stat().st_size if group.keep.exists() else 0
            rows.append((group.keep, (self._relative(group.keep), f"{number}", "남김", format_bytes(size)), KEPT_ROW))
            for duplicate in group.duplicates:
                rows.append((duplicate, (self._relative(duplicate), f"{number}", "지울 후보", format_bytes(size)), FLAGGED_ROW))
                total += 1
                saved += size
        self._summary.setPlainText(
            f"내용이 같은 파일 {len(groups)}묶음 · 지울 후보 {total}개 ({format_bytes(saved)} 확보) — "
            "묶음마다 경로가 가장 앞선 1개는 남깁니다.\n아래 버튼을 눌러야 지웁니다(되돌릴 수 없음)."
        )
        self._pending = ("dup", groups, total)
        self._fill_table(("파일", "묶음", "처리", "크기"), rows)

    # --- 실행 ---------------------------------------------------------------------
    def _run_pending(self) -> None:
        if self._pending is None or self.is_busy or self._folder is None:
            return
        folder = self._folder
        kind = self._pending[0]
        if kind == "move":
            _, move_kind, rows, _ = self._pending
            action = _MOVES[move_kind][2]
            self._start(
                "run_move", "옮기는 중...",
                lambda progress: (
                    move_kind,
                    apply_move_plan(rows, log_folder=folder, action=action, on_progress=progress),
                    len(rows),
                ),
            )
        elif kind == "empty":
            folders = self._pending[1]
            listing = "\n".join(self._relative(p) for p in folders[:30]) + ("\n…" if len(folders) > 30 else "")
            if not self._confirm("빈 폴더 삭제 확인", f"다음 {len(folders)}개 빈 폴더를 삭제합니다:\n{listing}\n계속하시겠습니까?"):
                return
            remove_empty_folders(folders, log_folder=folder)
            self._last_operation = {"kind": "empty_folders", "folders": folders}
            self._show_nothing(f"빈 폴더 {len(folders)}개 삭제 완료")
            self._offer_next_steps()
        elif kind == "dup":
            groups = self._pending[1]
            candidates = [dup for group in groups for dup in group.duplicates]
            listing = "\n".join(self._relative(p) for p in candidates[:30]) + ("\n…" if len(candidates) > 30 else "")
            confirmed = self._confirm(
                "⚠️ 중복 파일 삭제 확인",
                f"다음 {len(candidates)}개 파일을 영구히 삭제합니다 (복구 불가):\n{listing}\n\n"
                "그룹마다 1개(보존 대상)는 남으므로 사진 자체가 사라지지는 않지만, "
                "삭제된 파일은 휴지통을 거치지 않고 즉시 사라집니다.\n계속하시겠습니까?",
            )
            if not confirmed:
                return
            deleted_count = delete_duplicate_files(groups, log_folder=folder)
            self._show_nothing(f"중복 파일 {deleted_count}개 삭제 완료")
            self._offer_next_steps()

    def _on_moved(self, move_kind: str, moved: list[MovePlanRow], planned: int) -> None:
        if moved:
            self._last_operation = {"kind": "move", "rows": moved}
        skipped = planned - len(moved)
        text = f"{_MOVES[move_kind][0]} 정리: {len(moved)}개 이동 완료"
        if skipped:
            text += f" · {skipped}개는 같은 이름이 있어 건너뜀"
        self._show_nothing(text)
        self._offer_next_steps()

    def _undo_last_operation(self) -> None:
        if self._last_operation is None or self.is_busy:
            return
        kind = self._last_operation["kind"]
        if kind == "move":
            rows = self._last_operation["rows"]
            restored = apply_move_plan(build_undo_plan(rows))
            text = f"{len(restored)}개 파일 되돌리기 완료"
            if len(restored) < len(rows):
                text += f" · {len(rows) - len(restored)}개는 원래 자리에 같은 이름이 생겨 그대로 둠"
            self._show_nothing(text)
        elif kind == "empty_folders":
            folders = self._last_operation["folders"]
            for folder in folders:
                folder.mkdir(parents=True, exist_ok=True)
            self._show_nothing(f"빈 폴더 {len(folders)}개 다시 생성(복구) 완료")
        self._last_operation = None
        self._refresh()

    # --- 작업 스레드 ----------------------------------------------------------------
    def _start(self, task: str, status: str, fn: Callable) -> None:
        self._show_nothing()
        self._task = task
        self._worker = FunctionWorker(fn, "폴더 정리")
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_done)
        self._worker.failed.connect(self._on_error)
        self._worker.cancelled.connect(self._on_cancelled)
        self._progress_bar.setMaximum(0)  # 진행률을 알리기 전까지는 움직이는 막대
        self._status_label.setText(status)
        self._worker.start()
        self._refresh()

    def _finish(self) -> str:
        task, self._task, self._worker = self._task, "", None
        self._progress_bar.setMaximum(1)
        self._progress_bar.setValue(1)
        self._status_label.setText("")
        return task

    def _on_done(self, value) -> None:
        task = self._finish()
        if task == "summary":
            self._show_nothing(format_folder_summary(value))
        elif task == "mismatch":
            self._show_mismatches(value)
        elif task.startswith("plan_"):
            self._show_move_plan(task[len("plan_"):], value)
        elif task == "empty":
            self._show_empty_folders(value)
        elif task == "dup":
            self._show_duplicates(value)
        elif task == "run_move":
            self._on_moved(*value)
        self._refresh()

    def _on_progress(self, done: int, total: int) -> None:
        self._progress_bar.setMaximum(total)
        self._progress_bar.setValue(done)

    def _cancel(self) -> None:
        if self._worker is not None:
            self._worker.request_cancel()
            self._status_label.setText("멈추는 중...")

    def _on_cancelled(self) -> None:
        self._finish()
        self._status_label.setText("취소했습니다")
        self._refresh()

    def _on_error(self, message: str) -> None:
        task = self._finish()
        if task == "run_move":
            message = "옮기다 멈췄습니다. 그때까지 옮긴 것은 organize_log_*.jsonl에 남아 있습니다.\n\n" + message
        self._show_nothing()
        show_error(self, "오류", message)

    def _confirm(self, title: str, message: str) -> bool:
        return QMessageBox.question(self, title, message) == QMessageBox.StandardButton.Yes

    def _refresh(self) -> None:
        busy = self.is_busy
        for button in self._action_buttons:
            button.setEnabled(not busy)
        self._cancel_button.setEnabled(busy and self._task in ("plan_date", "dup"))
        self._undo_button.setVisible(self._last_operation is not None)
        self._undo_button.setEnabled(not busy)
        pending = self._pending
        if pending is None:
            self._run_button.setVisible(False)
        else:
            if pending[0] == "move":
                text, danger = f"{_MOVES[pending[1]][0]} 정리 — {pending[3]}개 이동", False
                enabled = pending[3] > 0
            elif pending[0] == "empty":
                text, danger, enabled = f"빈 폴더 {len(pending[1])}개 삭제…", True, True
            else:
                text, danger, enabled = f"중복 파일 {pending[2]}개 삭제…", True, True
            self._run_button.setText(text)
            # 지우는 작업은 빨간 글자(강조색 대신)로 위험을 드러낸다
            (make_danger if danger else make_primary)(self._run_button)
            fit_text(self._run_button)
            self._run_button.setVisible(True)
            self._run_button.setEnabled(enabled and not busy)
        self._results.setCurrentIndex(1 if self._table.rowCount() else 0)
        self._guide.set_done([self._folder is not None, False, False])
