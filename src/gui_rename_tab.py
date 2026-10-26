"""M9: 여러 바디 사진을 촬영 시각순으로 정렬해 00001부터 제자리 리네임하는 탭."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui_dnd import extract_dropped_folders
from gui_widgets import FolderList, PathLabel, compact, make_primary, small_button, status_row
from gui_format import format_rename_apply_summary, format_rename_preview
from gui_settings import load_folder, load_folder_list, make_settings, save_folder, save_folder_list
from rename_by_time import (
    RenamePlanRow,
    RenameResult,
    apply_merge_plan,
    apply_rename_plan,
    build_merge_plan,
    build_rename_plan,
)


def _percent(done: int, total: int) -> int:
    return int(done / total * 100) if total else 0


class PlanWorker(QThread):
    progress = Signal(int, int)
    status = Signal(str)
    finished_ok = Signal(list)  # list[RenamePlanRow]
    failed = Signal(str)

    def __init__(
        self,
        input_dirs: list[Path],
        recursive: bool,
        merge_output_dir: Path | None,
        scan_cache: dict | None = None,
    ) -> None:
        super().__init__()
        self._input_dirs = input_dirs
        self._recursive = recursive
        self._merge_output_dir = merge_output_dir
        self._scan_cache = scan_cache

    def run(self) -> None:
        try:
            def on_scan_progress(done: int, total: int, path: Path) -> None:
                self.progress.emit(done, total)
                self.status.emit(f"스캔 중: {path.name} ({done}/{total}, {_percent(done, total)}%)")

            if self._merge_output_dir is not None:
                rows = build_merge_plan(
                    self._input_dirs,
                    self._merge_output_dir,
                    recursive=self._recursive,
                    on_progress=on_scan_progress,
                    cache=self._scan_cache,
                )
            else:
                rows = build_rename_plan(
                    self._input_dirs,
                    recursive=self._recursive,
                    on_progress=on_scan_progress,
                    cache=self._scan_cache,
                )
            self.finished_ok.emit(rows)
        except Exception as e:  # noqa: BLE001 - surfaced to the user, not swallowed
            self.failed.emit(str(e))


class ApplyWorker(QThread):
    progress = Signal(int, int)
    finished_ok = Signal(list)  # list[RenameResult]
    failed = Signal(str)

    def __init__(
        self, rows: list[RenamePlanRow], log_dir: Path, merge_output_dir: Path | None
    ) -> None:
        super().__init__()
        self._rows = rows
        self._log_dir = log_dir
        self._merge_output_dir = merge_output_dir

    def run(self) -> None:
        try:
            if self._merge_output_dir is not None:
                results = apply_merge_plan(
                    self._rows, self._merge_output_dir, on_progress=self.progress.emit
                )
            else:
                results = apply_rename_plan(
                    self._rows, log_dir=self._log_dir, on_progress=self.progress.emit
                )
            self.finished_ok.emit(results)
        except Exception as e:  # noqa: BLE001 - surfaced to the user, not swallowed
            self.failed.emit(str(e))


class RenameTab(QWidget):
    def __init__(self) -> None:
        super().__init__()

        self._input_dirs: list[Path] = []
        self._merge_output_dir: Path | None = None
        self._rows: list[RenamePlanRow] | None = None
        self._plan_worker: PlanWorker | None = None
        self._apply_worker: ApplyWorker | None = None
        self._busy = False
        # 같은 세션(앱 종료 전까지) 동안 이미 스캔한 파일은 EXIF를 다시 읽지
        # 않도록 재사용하는 인메모리 캐시. (mtime, size)가 바뀐 파일만 다시 읽음.
        self._scan_cache: dict = {}
        self.setAcceptDrops(True)
        self._settings = make_settings()

        self._folder_list = FolderList()
        add_button = small_button("+", "입력 폴더 추가")
        add_button.clicked.connect(self._add_folder)
        remove_button = small_button("−", "목록에서 고른 폴더 빼기 (폴더 자체는 그대로)")
        remove_button.clicked.connect(self._remove_selected_folder)

        self._recursive_checkbox = QCheckBox("하위 폴더까지 포함")
        self._recursive_checkbox.stateChanged.connect(lambda _: self._invalidate_preview())

        self._merge_checkbox = QCheckBox("한 폴더로 합치기 (원본 바디 폴더에서는 파일이 빠짐)")
        self._merge_checkbox.stateChanged.connect(self._on_merge_toggled)

        self._merge_output_label = PathLabel()
        self._merge_output_label.setEnabled(False)
        self._merge_output_button = compact(QPushButton("선택…"))
        self._merge_output_button.setEnabled(False)
        self._merge_output_button.clicked.connect(self._choose_merge_output_dir)
        self._create_merge_output_button = compact(QPushButton("새 폴더 만들기…"))
        self._create_merge_output_button.setEnabled(False)
        self._create_merge_output_button.clicked.connect(self._create_merge_output_dir)
        self._open_merge_output_button = compact(QPushButton("열기"))
        self._open_merge_output_button.setEnabled(False)
        self._open_merge_output_button.clicked.connect(self._open_merge_output_folder)

        self._preview_button = make_primary(QPushButton("미리보기"))
        self._preview_button.clicked.connect(self._run_preview)

        self._apply_button = make_primary(QPushButton("적용"))
        self._apply_button.setEnabled(False)
        self._apply_button.clicked.connect(self._run_apply)

        self._progress_bar = QProgressBar()
        self._status_label = QLabel("")
        self._summary_text = QPlainTextEdit()
        self._summary_text.setReadOnly(True)
        self._summary_text.setAcceptDrops(False)
        self._summary_text.setPlaceholderText("미리보기를 누르면 바뀔 파일 이름 목록이 여기에 나옵니다.")

        description_label = QLabel(
            "촬영 시각 순으로 정렬해 00001부터 번호를 붙입니다. 같은 초에 촬영된 파일이 "
            "겹치면 파일 경로 순으로 처리하고, 같은 파일명의 RAW는 자동으로 짝지어져 "
            "같은 번호를 받습니다. 적용 후에는 원래 파일명으로 되돌릴 수 없습니다."
        )
        description_label.setWordWrap(True)

        folder_buttons = QVBoxLayout()
        folder_buttons.addWidget(add_button)
        folder_buttons.addWidget(remove_button)
        folder_buttons.addStretch()
        folder_box = QHBoxLayout()
        folder_box.addWidget(self._folder_list)
        folder_box.addLayout(folder_buttons)
        input_layout = QVBoxLayout()
        input_layout.addLayout(folder_box)
        input_layout.addWidget(self._recursive_checkbox)
        input_group = QGroupBox("입력 폴더 (바디별로 여러 개, 사진+영상 함께 정렬)")
        input_group.setLayout(input_layout)

        merge_output_row = QHBoxLayout()
        merge_output_row.addWidget(self._merge_output_label, stretch=1)
        merge_output_row.addWidget(self._merge_output_button)
        merge_output_row.addWidget(self._create_merge_output_button)
        merge_output_row.addWidget(self._open_merge_output_button)
        merge_form = QFormLayout()
        merge_form.addRow(self._merge_checkbox)
        merge_form.addRow("합칠 폴더:", merge_output_row)
        merge_group = QGroupBox("출력")
        merge_group.setLayout(merge_form)

        top_row = QHBoxLayout()
        top_row.addWidget(input_group, stretch=1)
        top_row.addWidget(merge_group, stretch=1)

        button_row = QHBoxLayout()
        button_row.addWidget(self._preview_button)
        button_row.addSpacing(12)
        button_row.addLayout(status_row(self._progress_bar, self._status_label), stretch=1)
        button_row.addWidget(self._apply_button)

        layout = QVBoxLayout()
        layout.addWidget(description_label)
        layout.addLayout(top_row)
        layout.addLayout(button_row)
        layout.addWidget(self._summary_text, stretch=1)

        for folder in load_folder_list(self._settings, "rename/input_dirs"):
            self._input_dirs.append(folder)
            self._folder_list.addItem(str(folder))
        restored_merge_output = load_folder(self._settings, "rename/merge_output_dir")
        if restored_merge_output is not None:
            self._merge_output_dir = restored_merge_output
            self._merge_output_label.set_path(restored_merge_output)
            self._open_merge_output_button.setEnabled(self._merge_checkbox.isChecked())

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

    def _add_folder(self) -> None:
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
        save_folder_list(self._settings, "rename/input_dirs", self._input_dirs)

    def _remove_selected_folder(self) -> None:
        for item in self._folder_list.selectedItems():
            index = self._folder_list.row(item)
            self._folder_list.takeItem(index)
            del self._input_dirs[index]
        self._invalidate_preview()
        save_folder_list(self._settings, "rename/input_dirs", self._input_dirs)

    def _on_merge_toggled(self, _state: int) -> None:
        checked = self._merge_checkbox.isChecked()
        self._merge_output_label.setEnabled(checked)
        self._merge_output_button.setEnabled(checked)
        self._create_merge_output_button.setEnabled(checked)
        self._open_merge_output_button.setEnabled(checked and self._merge_output_dir is not None)
        if not checked:
            self._merge_output_dir = None
            self._merge_output_label.set_path(None)
            save_folder(self._settings, "rename/merge_output_dir", None)
        self._invalidate_preview()

    def _choose_merge_output_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "병합 출력 폴더 선택")
        if directory:
            self._merge_output_dir = Path(directory)
            self._merge_output_label.set_path(Path(directory))
            self._open_merge_output_button.setEnabled(True)
            self._invalidate_preview()
            save_folder(self._settings, "rename/merge_output_dir", self._merge_output_dir)

    def _create_merge_output_dir(self) -> None:
        parent = QFileDialog.getExistingDirectory(self, "새 폴더를 만들 위치 선택")
        if not parent:
            return

        name, ok = QInputDialog.getText(self, "새 폴더 만들기", "폴더 이름:")
        if not ok or not name.strip():
            return

        new_dir = Path(parent) / name.strip()
        if new_dir.exists():
            QMessageBox.warning(self, "이미 존재함", f"이미 존재하는 폴더입니다: {new_dir}")
            return

        try:
            new_dir.mkdir(parents=True)
        except OSError as e:
            QMessageBox.critical(self, "폴더 생성 실패", str(e))
            return

        self._merge_output_dir = new_dir
        self._merge_output_label.set_path(new_dir)
        self._open_merge_output_button.setEnabled(True)
        self._invalidate_preview()
        save_folder(self._settings, "rename/merge_output_dir", self._merge_output_dir)

    def _open_merge_output_folder(self) -> None:
        if self._merge_output_dir is not None:
            # macOS/Windows 공통으로 OS 기본 파일 탐색기에서 연다
            opened = QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(self._merge_output_dir))
            )
            if not opened:
                QMessageBox.warning(
                    self,
                    "폴더 열기 실패",
                    f"병합 출력 폴더를 열지 못했습니다: {self._merge_output_dir}",
                )

    def _invalidate_preview(self) -> None:
        self._rows = None
        self._apply_button.setEnabled(False)

    def _run_preview(self) -> None:
        if self._busy:
            QMessageBox.warning(
                self, "작업 진행 중", "다른 작업이 진행 중입니다. 완료될 때까지 기다려주세요."
            )
            return

        if not self._input_dirs:
            QMessageBox.warning(self, "입력 폴더 없음", "먼저 입력 폴더를 하나 이상 추가하세요.")
            return

        if self._merge_checkbox.isChecked() and self._merge_output_dir is None:
            QMessageBox.warning(self, "병합 출력 폴더 없음", "병합할 출력 폴더를 먼저 선택하세요.")
            return

        self._busy = True
        self._preview_button.setEnabled(False)
        self._progress_bar.setValue(0)
        self._status_label.setText("스캔 중...")
        self._summary_text.setPlainText("스캔 중...")

        merge_output_dir = self._merge_output_dir if self._merge_checkbox.isChecked() else None
        self._plan_worker = PlanWorker(
            list(self._input_dirs),
            self._recursive_checkbox.isChecked(),
            merge_output_dir,
            scan_cache=self._scan_cache,
        )
        self._plan_worker.progress.connect(self._on_progress)
        self._plan_worker.status.connect(self._status_label.setText)
        self._plan_worker.finished_ok.connect(self._on_preview_done)
        self._plan_worker.failed.connect(self._on_error)
        self._plan_worker.start()

    def _on_preview_done(self, rows: list[RenamePlanRow]) -> None:
        self._busy = False
        self._status_label.setText("완료")
        self._rows = rows
        self._preview_button.setEnabled(True)
        self._apply_button.setEnabled(bool(rows))
        self._summary_text.setPlainText(format_rename_preview(rows))

    def _run_apply(self) -> None:
        if self._busy:
            QMessageBox.warning(
                self, "작업 진행 중", "다른 작업이 진행 중입니다. 완료될 때까지 기다려주세요."
            )
            return

        if not self._rows:
            QMessageBox.warning(self, "미리보기 필요", "먼저 미리보기를 실행하세요.")
            return

        merge_output_dir = self._merge_output_dir if self._merge_checkbox.isChecked() else None
        if merge_output_dir is not None:
            confirm_text = (
                f"{len(self._rows)}개 파일을 {merge_output_dir}(으)로 이동하며 이름을 "
                "새로 붙입니다 (원본 바디 폴더에서는 빠짐, 되돌릴 수 없음).\n계속하시겠습니까?"
            )
        else:
            confirm_text = (
                f"{len(self._rows)}개 파일의 이름을 원본 폴더에서 직접 바꿉니다 "
                "(파일 내용은 그대로, 원래 이름은 되돌릴 수 없음).\n계속하시겠습니까?"
            )
        confirm = QMessageBox.question(self, "적용 확인", confirm_text)
        if confirm != QMessageBox.StandardButton.Yes:
            return

        self._busy = True
        self._apply_button.setEnabled(False)
        self._progress_bar.setValue(0)

        log_dir = self._input_dirs[0]
        self._apply_worker = ApplyWorker(self._rows, log_dir, merge_output_dir)
        self._apply_worker.progress.connect(self._on_progress)
        self._apply_worker.finished_ok.connect(self._on_apply_done)
        self._apply_worker.failed.connect(self._on_error)
        self._apply_worker.start()

    def _on_apply_done(self, results: list[RenameResult]) -> None:
        self._busy = False
        self._apply_button.setEnabled(True)
        self._summary_text.setPlainText(format_rename_apply_summary(results))
        # 적용 후엔 파일명이 이미 바뀌었으므로 이전 계획으로 다시 적용하면 안 됨.
        self._rows = None
        self._apply_button.setEnabled(False)

    def _on_progress(self, done: int, total: int) -> None:
        self._progress_bar.setMaximum(total)
        self._progress_bar.setValue(done)

    def _on_error(self, message: str) -> None:
        self._busy = False
        self._preview_button.setEnabled(True)
        self._apply_button.setEnabled(bool(self._rows))
        QMessageBox.critical(self, "오류", message)
