"""카드 가져오기 화면: 메모리카드의 사진·RAW·영상을 촬영 날짜별 폴더로 복사하고(백업 드라이브에도),
복사본을 해시로 확인한다(card_import.py). 카드의 파일은 읽기만 한다.

미리보기(무엇을 어디로, 이미 가져온 것) → 표로 확인 → "가져오기"로만 복사한다. 끝나면 전부 확인됐는지
("카드를 지워도 안전") 아니면 무엇이 실패했는지 보여준다.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFormLayout,
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

import app_log
import disk_space
from card_import import (
    STATUS_EXISTS,
    STATUS_LABELS,
    ImportOutcome,
    ImportPlan,
    apply_import,
    build_import_plan,
    find_cards,
)
from gui_dnd import extract_dropped_folders
from gui_errors import folders_overlap, show_error
from gui_format import format_bytes, space_warning
from gui_navigation import NextStep, NextStepBar
from gui_reveal import add_file_actions
from gui_settings import load_bool, load_folder, make_settings, pick_directory, save_bool, save_folder
from gui_widgets import (
    FLAGGED_ROW,
    EtaProgressBar,
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

SOURCE_KEY = "import/source"
DESTINATION_KEY = "import/destination"
BACKUP_KEY = "import/backup"
USE_BACKUP_KEY = "import/use_backup"

_COLUMNS = ("카드 안 파일", "촬영 시각", "가져올 곳", "상태", "백업", "크기")
_PATH_ROLE = Qt.ItemDataRole.UserRole + 1

log = app_log.get("card_import")


class CardImportTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._settings = make_settings()
        self._source: Path | None = load_folder(self._settings, SOURCE_KEY)
        self._destination: Path | None = load_folder(self._settings, DESTINATION_KEY)
        self._backup: Path | None = load_folder(self._settings, BACKUP_KEY)
        self._plan: ImportPlan | None = None
        self._outcome: ImportOutcome | None = None
        self._worker: FunctionWorker | None = None
        self._busy = False
        self._importing = False
        self.setAcceptDrops(True)

        self._source_label = PathLabel("(카드를 고르거나 끌어다 놓으세요)")
        self._source_label.set_path(self._source)
        source_button = compact(QPushButton("선택…"))
        source_button.clicked.connect(self._choose_source)
        self._find_button = compact(QPushButton("카드 찾기"))
        self._find_button.setToolTip("<p>연결된 메모리카드(맨 위에 DCIM 폴더가 있는 드라이브)를 찾습니다.</p>")
        self._find_button.clicked.connect(self._find_cards)
        self._destination_label = PathLabel()
        self._destination_label.set_path(self._destination)
        destination_button = compact(QPushButton("선택…"))
        destination_button.clicked.connect(self._choose_destination)
        self._open_button = compact(QPushButton("열기"))
        self._open_button.clicked.connect(lambda: self._open(self._destination))
        self._backup_checkbox = QCheckBox("백업도:")
        self._backup_checkbox.setChecked(load_bool(self._settings, USE_BACKUP_KEY, False))
        self._backup_checkbox.setToolTip("<p>다른 드라이브에 같은 구조로 한 번 더 복사하고 그것도 확인합니다.</p>")
        self._backup_checkbox.toggled.connect(self._on_backup_toggled)
        self._backup_label = PathLabel("(다른 드라이브 권장)")
        self._backup_label.set_path(self._backup)
        backup_button = compact(QPushButton("선택…"))
        backup_button.clicked.connect(self._choose_backup)

        note = QLabel(
            "촬영 날짜별 폴더(2026-11-03)로 복사 · 카드는 그대로 · 복사본은 원본과 같은지 확인(해시) · "
            "이미 가져온 사진은 건너뜀 · 같은 이름의 다른 사진은 카드 폴더 이름을 붙여 따로"
        )
        note.setWordWrap(True)
        note.setEnabled(False)

        self._preview_button = make_primary(QPushButton("미리보기"))
        self._preview_button.setToolTip("<p>무엇을 어디로 복사할지 보여줍니다. 아직 아무것도 복사하지 않습니다.</p>")
        self._preview_button.clicked.connect(self._run_preview)
        self._cancel_button = compact(QPushButton("취소"))
        self._cancel_button.setEnabled(False)
        self._cancel_button.clicked.connect(self._cancel)
        self._import_button = make_primary(QPushButton("가져오기"))
        self._import_button.clicked.connect(self._run_import)
        self._progress_bar = EtaProgressBar()
        self._status_label = QLabel("")
        self._summary = SummaryLabel()
        self._result_label = QLabel()
        self._result_label.setWordWrap(True)
        self._result_label.setVisible(False)
        self._next_steps = NextStepBar()

        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_menu)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, len(_COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self._guide = GuidePanel(
            "카드 가져오기 순서",
            [
                "카드 고르기 — \"카드 찾기\"나 끌어다 놓기",
                "가져올 곳 고르기 (사진을 모아 두는 폴더 — 날짜별 폴더가 그 안에 생깁니다)",
                "백업할 다른 드라이브 고르기 (선택)",
                "미리보기 — 무엇을 어디로 복사할지, 이미 가져온 사진 확인",
                "가져오기 — 끝나면 \"카드를 지워도 안전\"인지 알려 줍니다",
            ],
        )
        self._results = QStackedWidget()
        self._results.addWidget(self._guide)
        self._results.addWidget(self._table)

        source_row = QHBoxLayout()
        source_row.addWidget(self._source_label, stretch=1)
        source_row.addWidget(self._find_button)
        source_row.addWidget(source_button)
        destination_row = QHBoxLayout()
        destination_row.addWidget(self._destination_label, stretch=1)
        destination_row.addWidget(destination_button)
        destination_row.addWidget(self._open_button)
        backup_row = QHBoxLayout()
        backup_row.addWidget(self._backup_label, stretch=1)
        backup_row.addWidget(backup_button)
        form = QFormLayout()
        form.addRow("카드:", source_row)
        form.addRow("가져올 곳:", destination_row)
        form.addRow(self._backup_checkbox, backup_row)
        form.addRow(note)
        self._warning = WarningLabel()
        form.addRow(self._warning)
        group = QGroupBox("폴더")
        group.setLayout(form)

        button_row = QHBoxLayout()
        button_row.addWidget(self._preview_button)
        button_row.addWidget(self._cancel_button)
        button_row.addSpacing(12)
        button_row.addLayout(status_row(self._progress_bar, self._status_label), stretch=1)
        button_row.addWidget(self._import_button)

        layout = QVBoxLayout()
        layout.addWidget(group)
        layout.addLayout(button_row)
        layout.addWidget(self._result_label)
        layout.addWidget(self._summary)
        layout.addWidget(self._next_steps)
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
            self._set_source(folders[0])
        event.acceptProposedAction()

    def receive_folders(self, folders: list[Path], replace: bool = True) -> None:
        """최근 폴더 메뉴 등에서 넘어올 때(첫 폴더를 가져올 곳으로 — 카드는 매번 바뀜)."""
        if folders:
            self._set_destination(folders[0])

    def _choose_source(self) -> None:
        directory = pick_directory(self, "메모리카드(또는 가져올 폴더) 선택")
        if directory:
            self._set_source(Path(directory))

    def _find_cards(self) -> None:
        cards = find_cards()
        if not cards:
            QMessageBox.information(
                self, "카드 찾기",
                "연결된 메모리카드를 찾지 못했습니다(맨 위에 DCIM 폴더가 있는 드라이브).\n"
                "카드를 꽂았는데도 안 보이면 \"선택…\"으로 직접 고르세요.",
            )
            return
        if len(cards) == 1:
            self._set_source(cards[0])
            return
        menu = QMenu(self)
        for card in cards:
            menu.addAction(str(card)).triggered.connect(lambda _checked=False, c=card: self._set_source(c))
        menu.exec(self._find_button.mapToGlobal(self._find_button.rect().bottomLeft()))

    def _set_source(self, folder: Path) -> None:
        self._source = folder
        self._source_label.set_path(folder)
        save_folder(self._settings, SOURCE_KEY, folder)
        self._invalidate()

    def _choose_destination(self) -> None:
        directory = pick_directory(self, "사진을 모아 둘 폴더 선택")
        if directory:
            self._set_destination(Path(directory))

    def _set_destination(self, folder: Path) -> None:
        self._destination = folder
        self._destination_label.set_path(folder)
        save_folder(self._settings, DESTINATION_KEY, folder)
        self._invalidate()

    def _choose_backup(self) -> None:
        directory = pick_directory(self, "백업할 폴더 선택 (다른 드라이브 권장)")
        if directory:
            self._backup = Path(directory)
            self._backup_label.set_path(self._backup)
            save_folder(self._settings, BACKUP_KEY, self._backup)
            self._backup_checkbox.setChecked(True)
            self._invalidate()

    def _on_backup_toggled(self, checked: bool) -> None:
        save_bool(self._settings, USE_BACKUP_KEY, checked)
        self._invalidate()

    def _backup_dir(self) -> Path | None:
        return self._backup if self._backup_checkbox.isChecked() else None

    def _open(self, folder: Path | None) -> None:
        if folder is not None and not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
            QMessageBox.warning(self, "폴더 열기 실패", f"폴더를 열지 못했습니다: {folder}")

    def _invalidate(self) -> None:
        self._plan = None
        self._outcome = None
        self._next_steps.clear()
        self._summary.setPlainText("")
        self._result_label.setVisible(False)
        self._table.setRowCount(0)
        self._refresh()

    # --- 미리보기 -----------------------------------------------------------------
    def _run_preview(self) -> None:
        if self._busy:
            return
        if self._source is None or self._destination is None:
            QMessageBox.warning(self, "폴더 필요", "카드와 가져올 곳을 먼저 고르세요.")
            return
        if self._backup_checkbox.isChecked() and self._backup is None:
            QMessageBox.warning(self, "백업 폴더 필요", "백업할 폴더를 고르거나 \"백업도\"를 끄세요.")
            return
        source, destination, backup = self._source, self._destination, self._backup_dir()
        self._start(
            lambda on_progress: build_import_plan(source, destination, backup, on_progress),
            "카드 훑는 중...",
            self._on_plan_done,
        )

    def _on_plan_done(self, plan: ImportPlan) -> None:
        self._set_busy(False)
        self._plan = plan
        self._outcome = None
        self._status_label.setText("완료")
        self._populate()
        self._refresh()

    def _populate(self) -> None:
        plan = self._plan
        if plan is None:
            return
        rows = plan.rows
        new = [r for r in rows if r.status != STATUS_EXISTS]
        dates = sorted({r.relative.parts[0] for r in rows})
        lines = [f"카드에서 {len(rows)}개 (날짜 {len(dates)}개: {', '.join(dates[:6])}{' …' if len(dates) > 6 else ''})"]
        lines.append(
            f"새로 복사 {len(new)}개 · 이미 가져옴 {len(rows) - len(new)}개 · 쓸 용량 {format_bytes(plan.total_bytes)}"
        )
        if plan.backup is not None:
            backup_new = sum(1 for r in rows if r.backup_status != STATUS_EXISTS)
            lines.append(f"백업: 새로 복사 {backup_new}개 → {plan.backup}")
        if plan.skipped_other:
            lines.append(f"사진·RAW·영상이 아닌 파일 {plan.skipped_other}개는 가져오지 않습니다.")
        self._summary.setPlainText("\n".join(lines))
        self._table.setUpdatesEnabled(False)
        self._table.setRowCount(len(rows))
        muted = self.palette().placeholderText()
        for i, row in enumerate(rows):
            try:
                shown = str(row.source.relative_to(plan.source))
            except ValueError:
                shown = row.source.name
            values = (
                shown,
                row.taken.strftime("%Y-%m-%d %H:%M:%S"),
                str(row.relative),
                STATUS_LABELS[row.status],
                STATUS_LABELS[row.backup_status] if row.backup_status else "—",
                format_bytes(row.size),
            )
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setData(_PATH_ROLE, str(row.source))
                if column == 0:
                    item.setToolTip(str(row.source))
                if column == 5:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if row.status == STATUS_EXISTS and row.backup_status in (None, STATUS_EXISTS):
                    item.setForeground(muted)
                self._table.setItem(i, column, item)
        self._table.setUpdatesEnabled(True)

    # --- 가져오기 -----------------------------------------------------------------
    def _run_import(self) -> None:
        plan = self._plan
        if self._busy or plan is None or self._outcome is not None:
            return
        new = len(plan.to_copy)
        lines = [f"카드의 {len(plan.rows)}개 중 {new}개를 복사합니다(이미 가져온 것은 확인만).",
                 f"가져올 곳: {plan.destination}"]
        if plan.backup is not None:
            lines.append(f"백업: {plan.backup}")
        lines.append("\n카드의 파일은 그대로 두고, 복사본은 원본과 같은지 하나하나 확인합니다. 계속하시겠습니까?")
        if QMessageBox.question(self, "카드 가져오기", "\n".join(lines)) != QMessageBox.StandardButton.Yes:
            return
        self._importing = True
        self._start(lambda on_progress: apply_import(plan, on_progress), "복사하고 확인하는 중...", self._on_import_done)

    def _on_import_done(self, outcome: ImportOutcome) -> None:
        self._importing = False
        self._set_busy(False)
        self._outcome = outcome
        self._show_outcome(outcome)
        self._refresh()

    def _show_outcome(self, outcome: ImportOutcome, stopped: bool = False) -> None:
        results = outcome.results
        copied = sum(1 for r in results if r.copied)
        backed_up = sum(1 for r in results if r.backed_up)
        existing = sum(1 for r in results if not r.copied and not r.error)
        text = f"복사 {copied}개 · 이미 있음(확인함) {existing}개"
        if self._plan is not None and self._plan.backup is not None:
            text += f" · 백업 {backed_up}개"
        if outcome.errors:
            text += f" · 실패 {len(outcome.errors)}개"
        self._status_label.setText(text)
        if outcome.safe_to_erase:
            self._result_label.setText(
                f"✓ {len(results)}개 모두 원본과 같은지 확인했습니다 — 카드를 지워도 안전합니다 "
                "(지우기는 카메라에서 포맷하세요)."
            )
            self._result_label.setStyleSheet("color: #2e7d32; font-weight: bold;")
        else:
            reason = "멈췄습니다" if stopped else f"{len(outcome.errors)}개를 가져오지 못했습니다"
            self._result_label.setText(f"⚠ {reason} — 카드를 지우지 마세요. 기록: {outcome.log_file}")
            self._result_label.setStyleSheet("color: #c62828; font-weight: bold;")
        self._result_label.setVisible(True)
        if outcome.errors:
            details = "\n".join(f"{r.row.source.name}: {r.error}" for r in outcome.errors[:20])
            self._summary.setPlainText(f"가져오지 못한 파일:\n{details}")
            failed = {str(r.row.source) for r in outcome.errors}
            for i in range(self._table.rowCount()):
                if self._table.item(i, 0).data(_PATH_ROLE) in failed:
                    paint_row(self._table, i, FLAGGED_ROW)
        dates = sorted({r.destination.parent for r in results if r.destination is not None and not r.error})
        if dates:
            self._next_steps.set_steps([
                NextStep("rename", tuple(dates), "가져온 날짜 폴더"),
                NextStep("selection", tuple(dates), "가져온 날짜 폴더"),
            ])

    # --- 공통 ---------------------------------------------------------------------
    def _start(self, fn, status: str, on_done) -> None:
        self._set_busy(True)
        self._status_label.setText(status)
        self._worker = FunctionWorker(fn, "카드 가져오기")
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(on_done)
        self._worker.failed.connect(self._on_error)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.start()

    def _cancel(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.request_cancel()
        self._cancel_button.setEnabled(False)
        self._status_label.setText("멈추는 중...")

    def _on_cancelled(self) -> None:
        importing = self._importing
        self._importing = False
        self._set_busy(False)
        self._status_label.setText("취소했습니다")
        if importing:
            # 이미 복사·확인한 파일은 남는다. 다시 미리보기하면 그것들은 "이미 가져옴"으로 나온다
            self._outcome = ImportOutcome([], None)
            self._result_label.setText("⚠ 가져오기를 멈췄습니다 — 카드를 지우지 마세요. 다시 미리보기하면 이어서 가져옵니다.")
            self._result_label.setStyleSheet("color: #c62828; font-weight: bold;")
            self._result_label.setVisible(True)
        self._refresh()

    def _on_progress(self, done: int, total: int) -> None:
        self._progress_bar.setMaximum(total)
        self._progress_bar.setValue(done)

    def _on_error(self, message: str) -> None:
        if self._importing:
            self._outcome = ImportOutcome([], None)  # 다시 미리보기부터
        self._importing = False
        self._set_busy(False)
        self._status_label.setText("")
        show_error(self, "카드 가져오기 오류", message)
        self._refresh()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._cancel_button.setEnabled(busy)
        if busy:
            self._progress_bar.setValue(0)
        self._refresh()

    def _warnings(self) -> str:
        source, destination, backup = self._source, self._destination, self._backup_dir()
        if source is not None and destination is not None and folders_overlap(source, destination):
            return "⚠ 가져올 곳이 카드 폴더와 같거나 서로 안에 들어 있어 가져올 수 없습니다."
        if backup is not None:
            if source is not None and folders_overlap(source, backup):
                return "⚠ 백업 폴더가 카드 폴더와 겹칩니다."
            if destination is not None and folders_overlap(destination, backup):
                return "⚠ 백업 폴더가 가져올 곳과 겹칩니다 — 다른 드라이브를 고르세요."
            if destination is not None and disk_space.same_drive(destination, backup):
                return "⚠ 백업이 가져올 곳과 같은 드라이브입니다 — 드라이브가 고장 나면 둘 다 잃습니다."
        plan = self._plan
        if plan is not None and self._outcome is None:
            needed_main = sum(r.size for r in plan.rows if r.status != STATUS_EXISTS)
            warning = space_warning(disk_space.check(plan.destination, needed_main), "가져올 곳")
            if not warning and plan.backup is not None:
                needed_backup = sum(r.size for r in plan.rows if r.backup_status != STATUS_EXISTS)
                if disk_space.same_drive(plan.destination, plan.backup):
                    needed_backup += needed_main
                warning = space_warning(disk_space.check(plan.backup, needed_backup), "백업")
            return warning
        return ""

    def _refresh(self) -> None:
        self._warning.set_warning(self._warnings())
        plan = self._plan
        count = len(plan.to_copy) if plan is not None and self._outcome is None else 0
        self._preview_button.setEnabled(not self._busy)
        self._find_button.setEnabled(not self._busy)
        ready = plan is not None and self._outcome is None and bool(plan.rows)
        self._import_button.setText(f"가져오기 ({count}개)" if ready else "가져오기")
        fit_text(self._import_button)
        self._import_button.setEnabled(ready and not self._busy)
        self._open_button.setEnabled(self._destination is not None)
        has_plan = plan is not None
        self._results.setCurrentWidget(self._table if has_plan else self._guide)
        self._guide.set_done([
            self._source is not None,
            self._destination is not None,
            self._backup_dir() is not None,
            has_plan,
            self._outcome is not None and self._outcome.safe_to_erase,
        ])

    def _show_menu(self, pos) -> None:
        item = self._table.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        add_file_actions(menu, self, Path(item.data(_PATH_ROLE)))
        menu.exec(self._table.viewport().mapToGlobal(pos))
