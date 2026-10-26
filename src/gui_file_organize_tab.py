"""사진 파일 정리 묶음: 시간순 정렬(M9)과 폴더 정리(M11). 어느 쪽을 보일지는 왼쪽 메뉴가
정한다(show_page)."""

from __future__ import annotations

from PySide6.QtWidgets import QStackedWidget

from gui_organize_tab import OrganizeTab
from gui_rename_tab import RenameTab


class FileOrganizeTab(QStackedWidget):
    PAGES = ("rename", "organize")

    def __init__(self) -> None:
        super().__init__()

        self._rename_tab = RenameTab()
        self._organize_tab = OrganizeTab()

        self.addWidget(self._rename_tab)
        self.addWidget(self._organize_tab)

    def show_page(self, name: str) -> None:
        self.setCurrentIndex(self.PAGES.index(name))

    @property
    def is_busy(self) -> bool:
        return self._rename_tab.is_busy or self._organize_tab.is_busy
