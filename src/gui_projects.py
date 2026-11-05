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
    QProgressDialog,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

import history
import projects
from batch_score import prescore
from gui_worker import CancellableWorker, PreviewCancelled
from gui_settings import pick_directory
from gui_widgets import compact, make_primary

_ID_ROLE = Qt.ItemDataRole.UserRole + 1


class _PrescoreWorker(CancellableWorker):
    progress = Signal(int, int, str, int, int)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, folders: list[Path]) -> None:
        super().__init__()
        self._folders = folders

    def run(self) -> None:
        def on_progress(*args) -> None:
            self.progress.emit(*args)
            self.check_cancel()

        try:
            self.finished_ok.emit(prescore(self._folders, on_progress))
        except PreviewCancelled:
            self.cancelled.emit()
        except Exception as e:  # noqa: BLE001 - 화면에 알림
            self.failed.emit(str(e))
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
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
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
        self._prescore_button = compact(QPushButton("미리 채점"))
        self._prescore_button.setToolTip(
            "<p>고른 촬영 건(여러 개는 Ctrl/⌘·Shift로)의 사진을 차례로 채점해 둡니다. 다 끝나면 열 때 "
            "베스트컷 결과가 바로 나옵니다 — 밤새 돌려 두기 좋습니다. 사진 파일은 그대로입니다.</p>"
        )
        self._prescore_button.clicked.connect(self._prescore)
        self._worker = None
        close_button = compact(QPushButton("닫기"))
        close_button.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addWidget(new_button)
        buttons.addWidget(self._rename_button)
        buttons.addWidget(self._folder_button)
        buttons.addWidget(self._remove_button)
        buttons.addWidget(self._prescore_button)
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

    def _selected_all(self) -> list[projects.Project]:
        ids = []
        for item in self._table.selectedItems():
            project_id = item.data(_ID_ROLE)
            if project_id not in ids:
                ids.append(project_id)
        by_id = {p.id: p for p in self._projects}
        return [by_id[i] for i in ids if i in by_id]

    def _selected(self) -> projects.Project | None:
        """하나만 골랐을 때 그 촬영 건(여럿이면 None — 열기·이름 바꾸기는 하나만)."""
        chosen = self._selected_all()
        return chosen[0] if len(chosen) == 1 else None

    def _refresh_buttons(self) -> None:
        project = self._selected()
        for button in (self._open_button, self._rename_button, self._folder_button, self._remove_button):
            button.setEnabled(project is not None)
        count = len(self._selected_all())
        self._prescore_button.setText(f"미리 채점 ({count}개)" if count > 1 else "미리 채점")
        self._prescore_button.setEnabled(count > 0 and self._worker is None)

    def _prescore(self) -> None:
        chosen = [p for p in self._selected_all() if p.root.exists()]
        if not chosen:
            QMessageBox.warning(self, "미리 채점", "고른 촬영 건의 폴더가 없습니다(옮겨졌거나 드라이브가 분리됨).")
            return
        progress = QProgressDialog("준비 중...", "멈추기", 0, 0, self)
        progress.setWindowTitle("미리 채점")
        progress.setMinimumWidth(460)
        progress.setMinimumDuration(0)
        names = [p.name for p in chosen]
        folders = [p.root for p in chosen]
        worker = _PrescoreWorker(folders)

        def on_progress(number: int, count: int, stage: str, done: int, total: int) -> None:
            progress.setLabelText(f"촬영 건 {number}/{count} · {names[number - 1]}\n{stage} ({done}/{total})")
            progress.setMaximum(max(total, 1))
            progress.setValue(done)

        def finish(message: str) -> None:
            progress.close()
            self._worker = None
            self._refresh_buttons()
            QMessageBox.information(self, "미리 채점", message)

        def on_done(results) -> None:
            lines = []
            for name, result in zip(names, results):
                if result.error:
                    lines.append(f"· {name}: 읽지 못함 — {result.error}")
                else:
                    lines.append(f"· {name}: {result.photos}장" + (f" (점수 못 냄 {result.failed}장)" if result.failed else ""))
            finish("채점을 마쳤습니다. 이제 열면 베스트컷 결과가 바로 나옵니다.\n" + "\n".join(lines))

        worker.progress.connect(on_progress)
        worker.finished_ok.connect(on_done)
        worker.failed.connect(lambda message: finish(f"채점하다 멈췄습니다: {message}\n그때까지 계산한 점수는 저장돼 있습니다."))
        worker.cancelled.connect(lambda: finish("멈췄습니다. 그때까지 계산한 점수는 저장돼 있어 다음에 이어서 계산합니다."))
        progress.canceled.connect(worker.request_cancel)
        self._worker = worker
        self._refresh_buttons()
        worker.start()

    def reject(self) -> None:  # noqa: D102 - 채점 중이면 닫지 않는다(스레드가 끝나야 함)
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, "미리 채점 중", "채점을 멈춘 뒤에 닫으세요.")
            return
        super().reject()

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
