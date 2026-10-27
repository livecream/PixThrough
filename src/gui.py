"""M8/M9/M10/M11: PixThrough 데스크톱 GUI. 기능마다 독립된 화면을 얇게 얹는 컨테이너.

왼쪽 메뉴에서 화면을 고른다. "사진 파일 정리" 묶음(시간순 정렬 M9, 폴더 정리 M11 —
gui_file_organize_tab.py)과 "사진 선별" 묶음(베스트컷 M8 + C컷 M10을 한
화면으로 — gui_photo_selection_tab.py). 각 화면의 실제 로직은 gui_bestcut_tab.py /
gui_rename_tab.py / gui_organize_tab.py에 있다.
예전 2단 탭(사진 파일 정리 > 시간순 정렬)을 한 번에 고르는 메뉴로 바꿨다.
"""

from __future__ import annotations

import sys

from dataclasses import dataclass

from PySide6.QtCore import QLibraryInfo, QLocale, QSize, Qt, QTranslator, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QFont
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QStyle,
    QVBoxLayout,
    QWidget,
)

import app_log
from gui_file_organize_tab import FileOrganizeTab
from gui_photo_selection_tab import PhotoSelectionTab
from gui_settings import make_settings
from gui_update import UpdateController
from version import __version__


# 결과 표가 넉넉히 보이는 처음 크기 (13인치 노트북 화면에도 들어감)
DEFAULT_SIZE = (1180, 780)
GEOMETRY_SETTINGS_KEY = "window/geometry"
PAGE_SETTINGS_KEY = "window/page"
SIDEBAR_WIDTH = 200
_PAGE_ROLE = Qt.ItemDataRole.UserRole + 1


@dataclass(frozen=True)
class Page:
    key: str  # 묶음 안에서의 화면 이름(show_page에 넘김), 마지막 화면 기억에도 씀
    title: str
    description: str
    icon: QStyle.StandardPixmap


# (묶음 제목, 화면들) — 실제 작업 순서대로: 파일 정리 → 고르기(베스트컷·C컷 한 화면)
MENU = (
    (
        "사진 파일 정리",
        (
            Page("rename", "시간순 정렬", "여러 바디의 사진·영상을 촬영 시각 순으로 00001부터 번호를 붙입니다.",
                 QStyle.StandardPixmap.SP_FileDialogDetailedView),
            Page("organize", "폴더 정리", "RAW·영상 분리, 날짜별 정리, 짝 안 맞는 파일·중복 파일·빈 폴더를 정리합니다.",
                 QStyle.StandardPixmap.SP_DirOpenIcon),
            Page("resize", "사진 축소", "사진의 긴 변을 정한 픽셀로 줄여 JPG로 새 폴더에 저장합니다. 원본은 그대로 둡니다.",
                 QStyle.StandardPixmap.SP_FileDialogContentsView),
        ),
    ),
    (
        "사진 선별",
        (
            Page("selection", "베스트컷·C컷 선별",
                 "장면마다 가장 좋은 컷을 골라 복사하고, 흐리거나 눈 감은 C컷은 걸러 따로 옮깁니다.",
                 QStyle.StandardPixmap.SP_DialogApplyButton),
        ),
    ),
)

_SIDEBAR_STYLE = """
QListWidget { border: none; background: transparent; outline: none; }
QListWidget::item { padding: 7px 8px; border-radius: 6px; }
QListWidget::item:selected { background: palette(highlight); color: palette(highlighted-text); }
"""


def install_korean_translation(app: QApplication) -> QTranslator | None:
    """Qt 기본 대화상자의 버튼(Yes/No, Show Details… 등)을 한국어로. 번역 파일이 없으면
    영어 그대로 둔다. 돌려준 객체는 앱이 끝날 때까지 살아 있어야 한다."""
    translator = QTranslator(app)
    path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    if not translator.load(QLocale(QLocale.Language.Korean), "qtbase", "_", path):
        return None
    app.installTranslator(translator)
    return translator


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"PixThrough v{__version__}")
        self.resize(DEFAULT_SIZE[0], DEFAULT_SIZE[1])
        self._settings = make_settings()
        # 마지막으로 닫을 때의 창 크기·위치. 모니터 구성이 바뀌어 화면 밖이면 Qt가 안으로 옮긴다.
        geometry = self._settings.value(GEOMETRY_SETTINGS_KEY)
        if geometry is not None:
            self.restoreGeometry(geometry)

        self._file_organize_tab = FileOrganizeTab()
        self._photo_selection_tab = PhotoSelectionTab()
        groups = (self._file_organize_tab, self._photo_selection_tab)

        self._pages = QStackedWidget()
        for group in groups:
            self._pages.addWidget(group)

        # 오른쪽 위: 지금 화면 제목과 한 줄 설명
        self._title = QLabel()
        title_font = QFont(self._title.font())
        title_font.setPointSizeF(title_font.pointSizeF() * 1.35)
        title_font.setBold(True)
        self._title.setFont(title_font)
        self._subtitle = QLabel()
        self._subtitle.setWordWrap(True)

        self._menu = QListWidget()
        self._menu.setFixedWidth(SIDEBAR_WIDTH)
        self._menu.setIconSize(QSize(18, 18))
        self._menu.setStyleSheet(_SIDEBAR_STYLE)
        header_font = QFont(self._menu.font())
        header_font.setBold(True)
        header_font.setPointSizeF(max(8.0, header_font.pointSizeF() - 1))
        for (group_title, pages), group in zip(MENU, groups):
            header = QListWidgetItem(group_title)
            header.setFlags(Qt.ItemFlag.NoItemFlags)  # 고를 수 없는 묶음 제목
            header.setFont(header_font)
            self._menu.addItem(header)
            for page in pages:
                item = QListWidgetItem(self.style().standardIcon(page.icon), page.title)
                item.setData(_PAGE_ROLE, (group, page))
                self._menu.addItem(item)
        self._menu.currentItemChanged.connect(self._on_menu_changed)

        for label in (self._title, self._subtitle):
            label.setContentsMargins(9, 0, 9, 0)  # 아래 화면들의 안쪽 여백과 맞춤
        content = QVBoxLayout()
        content.addWidget(self._title)
        content.addWidget(self._subtitle)
        content.addWidget(self._pages, stretch=1)
        body = QHBoxLayout()
        body.addWidget(self._menu)
        body.addLayout(content, stretch=1)
        central = QWidget()
        central.setLayout(body)
        self.setCentralWidget(central)

        self._select_page(self._settings.value(PAGE_SETTINGS_KEY, "rename"))

        self.updates = UpdateController(self, is_busy=self.is_busy)
        help_menu = self.menuBar().addMenu("도움말")
        self.updates.build_menu(help_menu)
        help_menu.addSeparator()
        open_logs = QAction("로그 폴더 열기", self)
        open_logs.setToolTip("앱 기록(pixthrough.log)과 업데이트 기록(update.log)이 있는 폴더")
        open_logs.triggered.connect(self.open_log_folder)
        help_menu.addAction(open_logs)

    def open_log_folder(self) -> None:
        folder = app_log.app_data_dir()
        folder.mkdir(parents=True, exist_ok=True)
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
            QMessageBox.warning(self, "폴더 열기 실패", f"폴더를 열지 못했습니다: {folder}")

    def show_unexpected_error(self, message: str) -> None:
        """잡지 못한 오류를 짧게 알린다(자세한 내용은 기록 파일에)."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle("예상하지 못한 오류")
        box.setText("예상하지 못한 오류가 났습니다. 하던 작업을 다시 시도해 보세요.")
        box.setInformativeText(
            f"{message}\n\n자세한 내용은 기록 파일({app_log.LOG_NAME})에 남았습니다. "
            "문제가 계속되면 이 파일을 보내 주세요."
        )
        open_button = box.addButton("로그 폴더 열기", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        if box.clickedButton() is open_button:
            self.open_log_folder()

    def _select_page(self, key: str) -> None:
        """메뉴에서 key 화면을 고른다(없는 이름이면 첫 화면)."""
        if key in PhotoSelectionTab.LEGACY_PAGES:  # 예전 "베스트컷 선별"/"C컷 정리" 메뉴
            key = "selection"
        first = None
        for row in range(self._menu.count()):
            data = self._menu.item(row).data(_PAGE_ROLE)
            if data is None:
                continue
            first = first if first is not None else row
            if data[1].key == key:
                self._menu.setCurrentRow(row)
                return
        self._menu.setCurrentRow(first)

    def _on_menu_changed(self, item: QListWidgetItem | None, _previous) -> None:
        data = item.data(_PAGE_ROLE) if item is not None else None
        if data is None:
            return
        group, page = data
        group.show_page(page.key)
        self._pages.setCurrentWidget(group)
        self._title.setText(page.title)
        self._subtitle.setText(page.description)
        self._settings.setValue(PAGE_SETTINGS_KEY, page.key)

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
        self._settings.setValue(GEOMETRY_SETTINGS_KEY, self.saveGeometry())
        event.accept()


def main() -> None:
    try:
        app_log.setup(__version__)
    except OSError:
        pass  # 기록 파일을 못 만들어도(권한 등) 앱은 그대로 쓴다
    app = QApplication(sys.argv)
    install_korean_translation(app)
    window = MainWindow()
    app_log.install_excepthooks(on_error=window.show_unexpected_error)
    window.show()
    window.updates.check_on_startup()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
