"""촬영 건 창(파일 › 촬영 건…): 촬영 건 목록과 진행 단계, 새로 만들기·열기·이름 바꾸기·목록에서 지우기.

열기를 누르면 opened(Project, 다음 화면) 신호를 보낸다 — 메인 창이 각 화면에 그 폴더를 넣는다.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

import history
import projects
from gui_settings import pick_directory
from gui_widgets import compact, make_primary

_ID_ROLE = Qt.ItemDataRole.UserRole + 1
_COLUMNS = ("촬영 건", "진행", "마지막 작업", "폴더")


def describe_progress(states: list[projects.StepState]) -> str:
    return " · ".join(("✓ " if s.done_at else "") + s.name for s in states)


class ProjectsDialog(QDialog):
    opened = Signal(object, str)  # Project, 열 화면

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("촬영 건")
        self.resize(940, 460)
        note = QLabel(
            "촬영 하나를 이름과 사진 폴더로 기억해, 다시 열면 각 화면에 그 폴더를 넣고 다음에 할 화면으로 갑니다. "
            "진행은 작업 기록에서 그 폴더 안 작업을 찾아 보여줍니다(✓ = 한 적 있음)."
        )
        note.setWordWrap(True)
        note.setEnabled(False)
        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.verticalHeader().setVisible(False)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self._table.setColumnWidth(3, 180)
        self._table.itemSelectionChanged.connect(self._refresh_buttons)
        self._table.itemDoubleClicked.connect(lambda _item: self._open())
        self._empty = QLabel("아직 촬영 건이 없습니다. \"새 촬영 건…\"으로 사진 폴더를 고르세요.")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)

        new_button = compact(QPushButton("새 촬영 건…"))
        new_button.clicked.connect(self._new)
        self._open_button = make_primary(QPushButton("열기"))
        self._open_button.clicked.connect(self._open)
        self._rename_button = compact(QPushButton("이름 바꾸기…"))
        self._rename_button.clicked.connect(self._rename)
        self._folder_button = compact(QPushButton("폴더 열기"))
        self._folder_button.clicked.connect(self._open_folder)
        self._remove_button = compact(QPushButton("목록에서 지우기"))
        self._remove_button.setToolTip("<p>목록에서만 지웁니다. 사진 폴더와 파일은 그대로입니다.</p>")
        self._remove_button.clicked.connect(self._remove)
        close_button = compact(QPushButton("닫기"))
        close_button.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addWidget(new_button)
        buttons.addWidget(self._rename_button)
        buttons.addWidget(self._folder_button)
        buttons.addWidget(self._remove_button)
        buttons.addStretch(1)
        buttons.addWidget(close_button)
        buttons.addWidget(self._open_button)

        layout = QVBoxLayout()
        layout.addWidget(note)
        layout.addWidget(self._table, stretch=1)
        layout.addWidget(self._empty, stretch=1)
        layout.addLayout(buttons)
        self.setLayout(layout)
        self._projects: list[projects.Project] = []
        self._states: dict[str, list[projects.StepState]] = {}
        self._reload()

    def _reload(self, select_id: str | None = None) -> None:
        self._projects = projects.load()
        entries = history.load()
        self._states = {p.id: projects.progress(p, entries) for p in self._projects}
        self._table.setRowCount(len(self._projects))
        for row, project in enumerate(self._projects):
            states = self._states[project.id]
            last = max((s.done_at for s in states if s.done_at), default=None)
            values = (
                project.name,
                describe_progress(states),
                last.strftime("%Y-%m-%d %H:%M") if last else "—",
                project.root.name or str(project.root),
            )
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setData(_ID_ROLE, project.id)
                if column == 1:
                    item.setToolTip("\n".join(
                        f"{'✓' if s.done_at else '·'} {s.name}"
                        + (f" — {s.done_at:%Y-%m-%d %H:%M} {s.summary}" if s.done_at else "")
                        for s in states
                    ))
                if column == 3:
                    item.setToolTip(str(project.root) + ("" if project.root.exists() else " (지금 없음)"))
                self._table.setItem(row, column, item)
        self._table.setVisible(bool(self._projects))
        self._empty.setVisible(not self._projects)
        index = next((i for i, p in enumerate(self._projects) if p.id == select_id), 0)
        if self._projects:
            self._table.selectRow(index)
        self._refresh_buttons()

    def _selected(self) -> projects.Project | None:
        items = self._table.selectedItems()
        if not items:
            return None
        project_id = items[0].data(_ID_ROLE)
        return next((p for p in self._projects if p.id == project_id), None)

    def _refresh_buttons(self) -> None:
        project = self._selected()
        for button in (self._open_button, self._rename_button, self._folder_button, self._remove_button):
            button.setEnabled(project is not None)

    def _new(self) -> None:
        directory = pick_directory(self, "촬영 건 사진 폴더 선택 (가장 위 폴더)")
        if not directory:
            return
        root = Path(directory)
        name, ok = QInputDialog.getText(self, "새 촬영 건", "이름 (예: 2026-11-03 김OO 본식):", text=root.name)
        if not ok:
            return
        project = projects.create(name, root)
        self._reload(project.id)

    def _open(self) -> None:
        project = self._selected()
        if project is None:
            return
        if not project.root.exists():
            QMessageBox.warning(self, "폴더 없음", f"촬영 건 폴더가 없습니다(옮겨졌거나 드라이브가 분리됨):\n{project.root}")
            return
        projects.touch(project.id)
        self.opened.emit(project, projects.next_page(self._states[project.id]))
        self.accept()

    def _rename(self) -> None:
        project = self._selected()
        if project is None:
            return
        name, ok = QInputDialog.getText(self, "이름 바꾸기", "촬영 건 이름:", text=project.name)
        if ok and name.strip():
            projects.rename(project.id, name)
            self._reload(project.id)

    def _open_folder(self) -> None:
        project = self._selected()
        if project is not None and not QDesktopServices.openUrl(QUrl.fromLocalFile(str(project.root))):
            QMessageBox.warning(self, "폴더 열기 실패", f"폴더를 열지 못했습니다:\n{project.root}")

    def _remove(self) -> None:
        project = self._selected()
        if project is None:
            return
        if QMessageBox.question(
            self, "목록에서 지우기", f"\"{project.name}\"을(를) 목록에서 지웁니다. 사진 폴더는 그대로입니다."
        ) == QMessageBox.StandardButton.Yes:
            projects.remove(project.id)
            self._reload()
