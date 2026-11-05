"""작업 기록 창(파일 › 작업 기록): history.jsonl을 최근 것부터 표로 보여주고, 고른 작업의 폴더나
기록 파일을 연다. 기록은 이 기능이 들어간 버전부터 앱에서 한 작업만 있다."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

import history
import history_undo
from gui_reveal import reveal_in_file_manager
from gui_widgets import compact

_ENTRY_ROLE = Qt.ItemDataRole.UserRole + 1
_COLUMNS = ("시각", "작업", "내용", "폴더")


class HistoryDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("작업 기록")
        self.resize(900, 520)
        self._entries = history.load()

        note = QLabel(
            "앱에서 한 파일 작업(복사·이동·되돌리기·정렬·정리·축소·라이트룸·고객 셀렉)을 최근 것부터 보여줍니다. "
            "자세한 내용은 각 작업의 기록 파일에 있습니다. 이 기능이 들어간 버전부터 기록됩니다."
        )
        note.setWordWrap(True)
        note.setEnabled(False)
        self._filter = QLineEdit()
        self._filter.setPlaceholderText("찾기 (작업·내용·폴더)")
        self._filter.setClearButtonEnabled(True)
        self._filter.textChanged.connect(self._apply_filter)

        self._table = QTableWidget(len(self._entries), len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.verticalHeader().setVisible(False)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        for row, entry in enumerate(self._entries):
            values = (
                entry.time.strftime("%Y-%m-%d %H:%M"),
                entry.kind,
                entry.summary,
                entry.folder.name if entry.folder else "",
            )
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setData(_ENTRY_ROLE, row)
                if column == 3 and entry.folder:
                    item.setToolTip(str(entry.folder))
                self._table.setItem(row, column, item)
        self._table.setColumnWidth(3, 200)
        self._table.itemSelectionChanged.connect(self._refresh_buttons)
        self._table.itemDoubleClicked.connect(lambda _item: self._open_folder())

        self._empty = QLabel("아직 기록된 작업이 없습니다.")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setVisible(not self._entries)
        self._table.setVisible(bool(self._entries))

        self._folder_button = compact(QPushButton("폴더 열기"))
        self._folder_button.clicked.connect(self._open_folder)
        self._log_button = compact(QPushButton("기록 파일 보기"))
        self._log_button.setToolTip("<p>작업의 자세한 기록(apply_log_*.jsonl 등) 파일을 Finder/탐색기에서 보여줍니다.</p>")
        self._log_button.clicked.connect(self._open_log)
        self._undo_button = compact(QPushButton("되돌리기…"))
        self._undo_button.setToolTip(
            "<p>고른 작업으로 옮기거나 이름을 바꾼 파일을 원래 자리·이름으로 되돌립니다(C컷·베스트컷 이동, "
            "폴더 정리 이동, 시간순 정렬). 원래 자리에 다른 파일이 있으면 덮어쓰지 않고 건너뜁니다. "
            "복사·삭제·라이트룸은 되돌리지 않습니다.</p>"
        )
        self._undo_button.clicked.connect(self._undo)
        close_button = compact(QPushButton("닫기"))
        close_button.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addWidget(self._folder_button)
        buttons.addWidget(self._log_button)
        buttons.addWidget(self._undo_button)
        buttons.addStretch(1)
        buttons.addWidget(close_button)

        layout = QVBoxLayout()
        layout.addWidget(note)
        layout.addWidget(self._filter)
        layout.addWidget(self._table, stretch=1)
        layout.addWidget(self._empty, stretch=1)
        layout.addLayout(buttons)
        self.setLayout(layout)
        if self._entries:
            self._table.selectRow(0)
        self._refresh_buttons()

    def _selected(self) -> history.Entry | None:
        items = self._table.selectedItems()
        return self._entries[items[0].data(_ENTRY_ROLE)] if items else None

    def _refresh_buttons(self) -> None:
        entry = self._selected()
        self._folder_button.setEnabled(bool(entry and entry.folder))
        self._log_button.setEnabled(bool(entry and entry.log_file))
        self._undo_button.setEnabled(bool(entry and history_undo.can_undo(entry)))

    def _undo(self) -> None:
        entry = self._selected()
        if entry is None or not history_undo.can_undo(entry):
            return
        try:
            plan = history_undo.build_undo_plan(entry)
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "되돌리기", f"기록 파일을 읽지 못했습니다:\n{e}")
            return
        ready = plan.ready
        if not ready:
            reasons = "\n".join(f"- {p.name}: {why}" for p, why in plan.problems[:8])
            QMessageBox.information(self, "되돌리기", "되돌릴 파일이 없습니다(이미 되돌렸거나 옮겨짐).\n" + reasons)
            return
        lines = [f"{entry.time:%Y-%m-%d %H:%M} {entry.kind} — {entry.summary}",
                 f"\n파일 {len(ready)}개를 원래 자리·이름으로 되돌립니다(같이 옮긴 RAW 포함)."]
        if plan.problems:
            lines.append(f"건너뜀 {len(plan.problems)}개 (원래 자리에 다른 파일이 있거나 파일이 없음):")
            lines += [f"  - {p.name}: {why}" for p, why in plan.problems[:6]]
        lines.append("\n열려 있는 화면의 미리보기는 다시 해야 합니다. 계속할까요?")
        if QMessageBox.question(self, "기록에서 되돌리기", "\n".join(lines)) != QMessageBox.StandardButton.Yes:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            result = history_undo.apply_undo(plan)
        finally:
            QApplication.restoreOverrideCursor()
        text = f"{len(result.restored)}개를 원래대로 되돌렸습니다."
        if result.skipped:
            text += f"\n건너뜀 {len(result.skipped)}개:\n" + "\n".join(
                f"- {p.name}: {why}" for p, why in result.skipped[:10]
            )
        if result.log_file:
            text += f"\n기록: {result.log_file.name}"
        QMessageBox.information(self, "기록에서 되돌리기", text)

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for row, entry in enumerate(self._entries):
            haystack = f"{entry.kind} {entry.summary} {entry.folder or ''}".lower()
            self._table.setRowHidden(row, bool(needle) and needle not in haystack)

    def _open_folder(self) -> None:
        entry = self._selected()
        if entry is None or entry.folder is None:
            return
        if not entry.folder.exists():
            QMessageBox.warning(self, "폴더 없음", f"폴더가 없습니다(옮겨졌거나 드라이브가 분리됨):\n{entry.folder}")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(entry.folder))):
            QMessageBox.warning(self, "폴더 열기 실패", f"폴더를 열지 못했습니다:\n{entry.folder}")

    def _open_log(self) -> None:
        entry = self._selected()
        if entry is None or entry.log_file is None:
            return
        path: Path = entry.log_file
        if not path.exists():
            QMessageBox.warning(self, "기록 파일 없음", f"기록 파일이 없습니다(옮겨졌거나 지워짐):\n{path}")
            return
        reveal_in_file_manager(path)
