"""사진 선별(베스트컷·C컷) 탭이 함께 쓰는 입력 폴더 칸.

보통 C컷을 먼저 걸러내고 같은 폴더에서 베스트컷을 고르는데, 예전에는 두 탭에 같은 폴더를
따로 넣어야 했다. 이 칸 하나를 두 탭 위에 두고, 바뀌면 changed 신호로 두 탭의 미리보기를
초기화한다. "하위 폴더까지 포함"도 여기 하나로 합쳤다(예전 베스트컷은 항상 포함, C컷만
체크박스 — 처음 값은 포함, 마지막 선택을 기억).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QFileDialog, QGroupBox, QHBoxLayout, QVBoxLayout

from gui_settings import load_bool, load_folder_list, make_settings, save_folder_list
from gui_widgets import FolderList, small_button

FOLDERS_KEY = "selection/input_dirs"
RECURSIVE_KEY = "selection/recursive"
# 공유하기 전 탭별로 저장하던 목록 — 처음 한 번만 옮겨 온다
LEGACY_FOLDER_KEYS = ("bestcut/input_dirs", "badcut/input_dirs")


class InputFolderPanel(QGroupBox):
    changed = Signal()

    def __init__(self, title: str = "입력 폴더") -> None:
        super().__init__(title)
        self._settings = make_settings()
        self._folders: list[Path] = []

        self._list = FolderList(rows=2)  # 두 탭 위에 늘 보이므로 얇게
        add_button = small_button("+", "입력 폴더 추가")
        add_button.clicked.connect(self._choose_folder)
        remove_button = small_button("−", "목록에서 고른 폴더 빼기 (폴더 자체는 그대로)")
        remove_button.clicked.connect(self._remove_selected)
        self._recursive_checkbox = QCheckBox("하위 폴더까지 포함")
        self._recursive_checkbox.setChecked(load_bool(self._settings, RECURSIVE_KEY, True))
        self._recursive_checkbox.toggled.connect(self._on_recursive_toggled)

        buttons = QVBoxLayout()
        buttons.addWidget(add_button)
        buttons.addWidget(remove_button)
        buttons.addStretch()
        side = QVBoxLayout()
        side.addWidget(self._recursive_checkbox)
        side.addStretch()
        self._side = side
        layout = QHBoxLayout()
        layout.addWidget(self._list, stretch=1)
        layout.addLayout(buttons)
        layout.addLayout(side)
        self.setLayout(layout)

        saved = load_folder_list(self._settings, FOLDERS_KEY)
        if not saved and self._settings.value(FOLDERS_KEY) is None:
            for key in LEGACY_FOLDER_KEYS:
                saved = load_folder_list(self._settings, key)
                if saved:
                    break
        self._set(saved, notify=False)

    def add_side_widget(self, widget) -> None:
        """오른쪽 칸(하위 폴더 체크박스 아래)에 버튼 등을 붙인다."""
        self._side.insertWidget(self._side.count() - 1, widget)

    def folders(self) -> list[Path]:
        return list(self._folders)

    def recursive(self) -> bool:
        return self._recursive_checkbox.isChecked()

    def add_folders(self, folders: list[Path]) -> None:
        new = [f for f in folders if f not in self._folders]
        if new:
            self._set(self._folders + new)

    def set_folders(self, folders: list[Path]) -> None:
        self._set(list(folders))

    def _set(self, folders: list[Path], notify: bool = True) -> None:
        self._folders = folders
        self._list.clear()
        for folder in folders:
            self._list.addItem(str(folder))
        save_folder_list(self._settings, FOLDERS_KEY, folders)
        if notify:
            self.changed.emit()

    def _choose_folder(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "입력 폴더 추가")
        if directory:
            self.add_folders([Path(directory)])

    def _remove_selected(self) -> None:
        rows = {self._list.row(item) for item in self._list.selectedItems()}
        if rows:
            self._set([f for i, f in enumerate(self._folders) if i not in rows])

    def _on_recursive_toggled(self, checked: bool) -> None:
        self._settings.setValue(RECURSIVE_KEY, checked)
        self.changed.emit()
