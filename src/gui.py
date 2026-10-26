"""M8/M9/M10/M11: PixThrough 데스크톱 GUI. 탭마다 독립된 기능을 얇게 얹는 컨테이너.

최상위 탭은 두 개: "사진 파일 정리"(시간순 정렬 M9 + 폴더 정리 M11을 하위 탭으로
묶음, gui_file_organize_tab.py)와 "사진 선별"(베스트컷 선별 M8 + C컷 정리 M10을
하위 탭으로 묶음, gui_photo_selection_tab.py). 각 하위 탭의 실제 로직은
gui_bestcut_tab.py / gui_rename_tab.py / gui_badcut_tab.py / gui_organize_tab.py에 있다.
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox, QTabWidget

from gui_file_organize_tab import FileOrganizeTab
from gui_photo_selection_tab import PhotoSelectionTab
from gui_update import UpdateController
from version import __version__


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"PixThrough v{__version__}")
        self.resize(640, 560)

        self._file_organize_tab = FileOrganizeTab()
        self._photo_selection_tab = PhotoSelectionTab()

        tabs = QTabWidget()
        tabs.addTab(self._file_organize_tab, "사진 파일 정리")
        tabs.addTab(self._photo_selection_tab, "사진 선별")
        self.setCentralWidget(tabs)

        self.updates = UpdateController(self, is_busy=self.is_busy)
        self.updates.build_menu(self.menuBar().addMenu("도움말"))

    def is_busy(self) -> bool:
        return self._file_organize_tab.is_busy or self._photo_selection_tab.is_busy

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override signature
        busy_tabs = [
            tab
            for tab in (self._file_organize_tab, self._photo_selection_tab)
            if tab.is_busy
        ]
        if busy_tabs:
            QMessageBox.warning(
                self,
                "작업 진행 중",
                "다른 작업이 진행 중입니다. 완료된 후 다시 닫아주세요.",
            )
            event.ignore()
            return
        event.accept()


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    window.updates.check_on_startup()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
