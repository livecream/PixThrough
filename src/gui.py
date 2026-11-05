"""M8/M9/M10/M11: PixThrough 데스크톱 GUI. 기능마다 독립된 화면을 얇게 얹는 컨테이너.

왼쪽 메뉴에서 화면을 고른다. "사진 파일 정리" 묶음(시간순 정렬 M9, 폴더 정리 M11 —
gui_file_organize_tab.py)과 "사진 선별" 묶음(베스트컷 M8 + C컷 M10을 한
화면으로 — gui_photo_selection_tab.py). 각 화면의 실제 로직은 gui_bestcut_tab.py /
gui_rename_tab.py / gui_organize_tab.py에 있다.
예전 2단 탭(사진 파일 정리 > 시간순 정렬)을 한 번에 고르는 메뉴로 바꿨다.
"""

from __future__ import annotations

import datetime
import sys

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QLibraryInfo, QLocale, QSize, Qt, QTimer, QTranslator, QUrl
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QProgressDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QStyle,
    QVBoxLayout,
    QWidget,
)

import app_log
import choice_store
import history
import projects
import settings_transfer
from gui_errors import show_error
from gui_file_organize_tab import FileOrganizeTab
from gui_history import HistoryDialog
from gui_appearance import (
    FONT_SCALE_KEY, FONT_SCALES, THEME_KEY, THEMES, apply_font_scale, apply_saved, apply_theme, font_scale, theme,
)
from gui_projects import ProjectsDialog
from gui_shoot_stats import StatsDialog
from gui_worker import FunctionWorker
from shoot_stats import collect, compute
from gui_help import ShortcutsDialog, WelcomeDialog, remember_welcome, should_show_welcome
from gui_navigation import FLOW_STEPS, FLOW_TITLES, navigator
from gui_photo_selection_tab import PhotoSelectionTab
from gui_settings import clear_recent, make_settings, pick_directory, recent_folders
from gui_update import UpdateController
from version import __version__


# 결과 표가 넉넉히 보이는 처음 크기 (13인치 노트북 화면에도 들어감)
DEFAULT_SIZE = (1180, 780)
GEOMETRY_SETTINGS_KEY = "window/geometry"
PAGE_SETTINGS_KEY = "window/page"
PROJECT_SETTINGS_KEY = "project/current"  # 마지막으로 연 촬영 건(창 제목)
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
            Page("import", "카드 가져오기",
                 "메모리카드의 사진·영상을 촬영 날짜별 폴더로 복사하고(백업 드라이브에도), 원본과 같은지 확인합니다.",
                 QStyle.StandardPixmap.SP_DriveFDIcon),
            Page("rename", "시간순 정렬", "여러 바디의 사진·영상을 촬영 시각 순으로 번호를 붙여 이름을 바꿉니다(이름 규칙은 직접 정할 수 있음).",
                 QStyle.StandardPixmap.SP_FileDialogDetailedView),
            Page("organize", "폴더 정리", "RAW·영상 분리, 날짜별 정리, 짝 안 맞는 파일·중복 파일·빈 폴더를 정리합니다.",
                 QStyle.StandardPixmap.SP_DirOpenIcon),
            Page("resize", "사진 축소", "사진의 긴 변을 정한 픽셀로 줄여 JPG로 새 폴더에 저장합니다. 원본은 그대로 둡니다.",
                 QStyle.StandardPixmap.SP_FileDialogContentsView),
            Page("proof", "고객 셀렉", "고객이 브라우저로 사진을 고르는 페이지(zip)를 만들고, 고른 결과대로 원본을 따로 복사합니다.",
                 QStyle.StandardPixmap.SP_DialogYesButton),
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
        # 한 번에 처리 중이면 위에 진행 줄(단계 ✓ · 지금 · 남은 것, 그만하기)
        self._flow_bar = QFrame()
        self._flow_bar.setObjectName("flowBar")
        self._flow_bar.setStyleSheet(
            "#flowBar { background: rgba(26,115,232,0.12); border: 1px solid rgba(26,115,232,0.45); border-radius: 6px; }"
        )
        self._flow_label = QLabel()
        self._flow_label.setWordWrap(True)
        self._flow_stop = QPushButton("그만하기")
        self._flow_stop.clicked.connect(lambda: navigator().stop_flow())
        self._flow_close = QPushButton("닫기")
        self._flow_close.clicked.connect(self._flow_bar.hide)
        flow_row = QHBoxLayout(self._flow_bar)
        flow_row.setContentsMargins(10, 4, 6, 4)
        flow_row.addWidget(self._flow_label, stretch=1)
        flow_row.addWidget(self._flow_stop)
        flow_row.addWidget(self._flow_close)
        self._flow_bar.hide()
        content = QVBoxLayout()
        content.addWidget(self._flow_bar)
        content.addWidget(self._title)
        content.addWidget(self._subtitle)
        content.addWidget(self._pages, stretch=1)
        body = QHBoxLayout()
        body.addWidget(self._menu)
        body.addLayout(content, stretch=1)
        central = QWidget()
        central.setLayout(body)
        self.setCentralWidget(central)

        self._current_page = "rename"
        self._select_page(self._settings.value(PAGE_SETTINGS_KEY, "rename"))

        navigator().requested.connect(self._navigate)
        navigator().flow_requested.connect(self._continue_flow)
        navigator().flow_changed.connect(self._update_flow_bar)
        # 화면 바꾸기 Ctrl(⌘)+1~6(왼쪽 메뉴 순서), 지금 화면 미리보기 Ctrl(⌘)+Enter (편의 6)
        keys = [p.key for _title, pages in MENU for p in pages]
        for number, key in enumerate(keys, start=1):
            QShortcut(QKeySequence(f"Ctrl+{number}"), self, activated=lambda k=key: self._select_page(k))
        for row in range(self._menu.count()):
            data = self._menu.item(row).data(_PAGE_ROLE)
            if data is not None:
                self._menu.item(row).setToolTip(f"{data[1].description} (Ctrl/⌘+{keys.index(data[1].key) + 1})")
        for sequence in ("Ctrl+Return", "Ctrl+Enter"):
            QShortcut(QKeySequence(sequence), self, activated=self._run_current_preview)

        self.updates = UpdateController(self, is_busy=self.is_busy)
        file_menu = self.menuBar().addMenu("파일")
        projects_action = QAction("촬영 건…", self)
        projects_action.setShortcut(QKeySequence("Ctrl+Shift+O"))
        projects_action.setToolTip("촬영 하나를 이름과 폴더로 기억해 이어서 작업합니다")
        projects_action.triggered.connect(self.show_projects)
        file_menu.addAction(projects_action)
        file_menu.addSeparator()
        current = self._settings.value(PROJECT_SETTINGS_KEY, "")
        project = next((p for p in projects.load() if p.id == current), None) if current else None
        self._set_project_title(project.name if project else None)
        self._recent_menu = file_menu.addMenu("최근 폴더")
        self._recent_menu.setToolTipsVisible(True)
        self._recent_menu.aboutToShow.connect(self._fill_recent_menu)
        history_action = QAction("작업 기록…", self)
        history_action.setToolTip("앱에서 한 복사·이동·정리 등 파일 작업을 최근 것부터 봅니다")
        history_action.triggered.connect(lambda: HistoryDialog(self).exec())
        file_menu.addAction(history_action)
        stats_action = QAction("촬영 통계…", self)
        stats_action.setToolTip("바디·렌즈·시간대·ISO·셔터 분포(베스트컷 미리보기가 있으면 C컷 비율까지)")
        stats_action.triggered.connect(self.show_shoot_stats)
        file_menu.addAction(stats_action)
        file_menu.addSeparator()
        client = QAction("고객 선택 결과 가져오기…", self)
        client.setToolTip("고객 셀렉 결과 파일을 베스트컷 화면의 미리보기 사진과 맞춰 \"고객\" 표시를 붙입니다")
        client.triggered.connect(self._import_client_result)
        file_menu.addAction(client)
        file_menu.addSeparator()
        export_settings = QAction("설정 내보내기…", self)
        export_settings.setToolTip("앱 설정과 직접 고른 컷·고친 장면·고객 선택 기록을 파일 하나로 저장합니다(점수는 빼고)")
        export_settings.triggered.connect(self._export_settings)
        file_menu.addAction(export_settings)
        import_settings = QAction("설정 가져오기…", self)
        import_settings.setToolTip("내보낸 설정 파일을 이 컴퓨터에 가져옵니다(가져오기 전 상태는 백업)")
        import_settings.triggered.connect(self._import_settings)
        file_menu.addAction(import_settings)
        self._build_view_menu()
        help_menu = self.menuBar().addMenu("도움말")
        welcome = QAction("처음 사용 안내…", self)
        welcome.triggered.connect(self.show_welcome)
        help_menu.addAction(welcome)
        shortcuts = QAction("단축키…", self)
        shortcuts.setShortcut(QKeySequence(Qt.Key.Key_F1))
        shortcuts.triggered.connect(self.show_shortcuts)
        help_menu.addAction(shortcuts)
        # ?는 글자 칸에서는 그대로 입력되고(칸이 단축키보다 먼저 받음) 나머지 자리에서 단축키 모음
        QShortcut(QKeySequence("?"), self, activated=self.show_shortcuts)
        help_menu.addSeparator()
        self.updates.build_menu(help_menu)
        help_menu.addSeparator()
        open_logs = QAction("로그 폴더 열기", self)
        open_logs.setToolTip("앱 기록(pixthrough.log)과 업데이트 기록(update.log)이 있는 폴더")
        open_logs.triggered.connect(self.open_log_folder)
        help_menu.addAction(open_logs)

    def show_shoot_stats(self) -> None:
        """베스트컷 미리보기가 있으면 그 사진(C컷·남김 비율 포함), 없으면 폴더를 골라 통계를 본다."""
        source = self._photo_selection_tab._best_cut_tab.stats_source()
        if source is not None:
            photos, flagged, kept = source
            StatsDialog(compute(photos, flagged, kept), "베스트컷·C컷 화면의 미리보기 사진", self).exec()
            return
        directory = pick_directory(self, "통계를 볼 사진 폴더 선택 (하위 폴더 포함)")
        if not directory:
            return
        folder = Path(directory)
        progress = QProgressDialog("촬영 정보 읽는 중...", "취소", 0, 0, self)
        progress.setWindowTitle("촬영 통계")
        progress.setMinimumDuration(300)
        worker = FunctionWorker(lambda on_progress: collect(folder, True, on_progress), "촬영 통계")

        def on_progress(done: int, total: int) -> None:
            progress.setMaximum(total)
            progress.setValue(done)

        def on_done(photos) -> None:
            progress.close()
            StatsDialog(compute(photos), f"{folder} (하위 폴더 포함)", self).exec()

        def on_failed(message: str) -> None:
            progress.close()
            show_error(self, "촬영 통계를 만들지 못했습니다", message)

        worker.progress.connect(on_progress)
        worker.finished_ok.connect(on_done)
        worker.failed.connect(on_failed)
        worker.cancelled.connect(progress.close)
        progress.canceled.connect(worker.request_cancel)
        self._stats_worker = worker
        worker.start()

    def show_projects(self) -> None:
        dialog = ProjectsDialog(self)
        dialog.opened.connect(self._open_project)
        dialog.exec()

    def _open_project(self, project, page: str) -> None:
        """촬영 건을 열면 각 화면에 그 폴더를 넣고 다음에 할 화면으로 간다."""
        if self.is_busy():
            QMessageBox.warning(self, "작업 진행 중", "작업이 끝난 뒤에 촬영 건을 여세요.")
            return
        root = project.root
        for key in FileOrganizeTab.PAGES:
            folders = projects.photo_folders(root) if key == "rename" else [root]
            self._file_organize_tab.page_widget(key).receive_folders(folders, replace=True)
        self._photo_selection_tab.receive_folders([root], replace=True)
        self._settings.setValue(PROJECT_SETTINGS_KEY, project.id)
        self._set_project_title(project.name)
        self._select_page(page)

    def _set_project_title(self, name: str | None) -> None:
        self.setWindowTitle(f"PixThrough v{__version__}" + (f" — {name}" if name else ""))

    def _export_settings(self) -> None:
        stamp = datetime.date.today().strftime("%Y%m%d")
        start = recent_folders(self._settings)[0] if recent_folders(self._settings) else Path.home()
        chosen, _ = QFileDialog.getSaveFileName(
            self, "설정 내보내기", str(start / f"pixthrough-settings_{stamp}.json"), "PixThrough 설정 (*.json)"
        )
        if not chosen:
            return
        path = Path(chosen)
        if path.suffix.lower() != ".json":
            path = path.with_name(path.name + ".json")
        try:
            summary = settings_transfer.export_to(self._settings, path)
        except OSError as e:
            show_error(self, "설정을 내보내지 못했습니다", str(e))
            return
        QMessageBox.information(
            self,
            "설정 내보내기",
            f"내보냈습니다: {summary.describe()}\n{path}\n\n"
            "다른 컴퓨터에서는 파일 › 설정 가져오기…로 가져옵니다. 저장된 점수는 넣지 않아서 "
            "처음 미리보기할 때 다시 계산합니다. 직접 고른 기록은 같은 경로에 같은 사진이 있을 때 쓰입니다.",
        )

    def _import_settings(self) -> None:
        if self.is_busy():
            QMessageBox.information(self, "설정 가져오기", "작업이 끝난 뒤에 가져오세요.")
            return
        start = recent_folders(self._settings)[0] if recent_folders(self._settings) else Path.home()
        chosen, _ = QFileDialog.getOpenFileName(self, "설정 가져오기", str(start), "PixThrough 설정 (*.json)")
        if not chosen:
            return
        try:
            data = settings_transfer.read_file(Path(chosen))
        except (OSError, ValueError) as e:
            show_error(self, "설정 파일을 읽지 못했습니다", str(e))
            return
        summary = settings_transfer.summarize(data)
        made = f" (PixThrough {data.get('app_version', '?')}, {str(data.get('exported', ''))[:16].replace('T', ' ')})"
        confirm = QMessageBox.question(
            self,
            "설정 가져오기",
            f"{Path(chosen).name}{made}\n{summary.describe()}\n\n"
            "파일에 있는 설정은 바꾸고(없는 설정은 그대로), 직접 고른 기록은 더합니다. "
            "지금 상태는 먼저 백업해 둡니다. 가져올까요?",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            imported, backup = settings_transfer.import_from(self._settings, Path(chosen))
        except (OSError, ValueError) as e:
            show_error(self, "설정을 가져오지 못했습니다", str(e))
            return
        choice_store.clear_memory()
        answer = QMessageBox.question(
            self,
            "설정 가져오기",
            f"가져왔습니다: {imported.describe()}\n가져오기 전 상태 백업: {backup}\n\n"
            "설정은 앱을 다시 켜야 화면에 모두 반영됩니다. 지금 앱을 닫을까요?",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.close()

    def _fill_recent_menu(self) -> None:
        """최근 폴더를 고르면 지금 화면의 입력 폴더로 넣는다."""
        menu = self._recent_menu
        menu.clear()
        folders = recent_folders(self._settings)
        if not folders:
            empty = menu.addAction("(아직 없음)")
            empty.setEnabled(False)
            return
        page = self._current_page
        heading = menu.addAction(f"지금 화면({self._title.text()})에 넣기:")
        heading.setEnabled(False)
        for folder in folders:
            action = menu.addAction(folder.name or str(folder))
            action.setToolTip(str(folder))
            action.triggered.connect(lambda _=False, f=folder: self._navigate(page, [f], replace=False))
        menu.addSeparator()
        menu.addAction("목록 지우기").triggered.connect(lambda: clear_recent(self._settings))

    def _import_client_result(self) -> None:
        self._select_page("selection")
        self._photo_selection_tab.import_client_result()

    def show_shortcuts(self) -> None:
        ShortcutsDialog(self).exec()

    def show_welcome(self) -> None:
        dialog = WelcomeDialog(self)
        dialog.page_requested.connect(self._select_page)
        dialog.exec()
        remember_welcome(dialog.show_again.isChecked())

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

    def _build_view_menu(self) -> None:
        """보기 › 글자 크기·테마(편의 7) — 고르면 바로 바꾸고 기억한다."""
        view_menu = self.menuBar().addMenu("보기")
        size_menu = view_menu.addMenu("글자 크기")
        size_group = QActionGroup(self)
        current = font_scale(self._settings)
        for percent, label in FONT_SCALES:
            action = size_menu.addAction(f"{label} ({percent}%)")
            action.setCheckable(True)
            action.setChecked(percent == current)
            action.triggered.connect(lambda _checked=False, p=percent: self._set_font_scale(p))
            size_group.addAction(action)
        theme_menu = view_menu.addMenu("테마")
        theme_group = QActionGroup(self)
        current_theme = theme(self._settings)
        for key, label in THEMES:
            action = theme_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(key == current_theme)
            action.triggered.connect(lambda _checked=False, k=key: self._set_theme(k))
            theme_group.addAction(action)

    def _set_font_scale(self, percent: int) -> None:
        self._settings.setValue(FONT_SCALE_KEY, percent)
        apply_font_scale(QApplication.instance(), percent)
        self._apply_title_fonts()

    def _set_theme(self, name: str) -> None:
        self._settings.setValue(THEME_KEY, name)
        if not apply_theme(QApplication.instance(), name):
            QMessageBox.information(self, "테마", "이 버전의 화면 라이브러리는 테마 바꾸기를 지원하지 않아, 다음에 켤 때도 시스템 설정을 따릅니다.")

    def _apply_title_fonts(self) -> None:
        """제목·메뉴 묶음 글자는 직접 정한 크기라 앱 글자 크기에 맞춰 다시 정한다."""
        base = QApplication.instance().font()
        title_font = QFont(base)
        title_font.setPointSizeF(base.pointSizeF() * 1.35)
        title_font.setBold(True)
        self._title.setFont(title_font)
        header_font = QFont(base)
        header_font.setBold(True)
        header_font.setPointSizeF(max(8.0, base.pointSizeF() - 1))
        for row in range(self._menu.count()):
            item = self._menu.item(row)
            if item.data(_PAGE_ROLE) is None:
                item.setFont(header_font)

    def _update_flow_bar(self) -> None:
        nav = navigator()
        if nav.flow_step is None:
            if nav.flow_note:
                self._flow_label.setText(f"한 번에 처리 · {nav.flow_note}")
                self._flow_stop.hide()
                self._flow_close.show()
                self._flow_bar.show()
            else:
                self._flow_bar.hide()
            return
        current = FLOW_STEPS.index(nav.flow_step)
        parts = []
        for i, step in enumerate(FLOW_STEPS):
            title = FLOW_TITLES[step]
            parts.append(f"✓ {title}" if i < current else f"<b>▶ {title}</b>" if i == current else title)
        hint = {
            "import": "표를 확인하고 \"가져오기\"를 누르세요",
            "rename": "표를 확인하고 \"적용\"을 누르세요",
            "selection": "미리보기가 끝나면 마칩니다",
        }[nav.flow_step]
        self._flow_label.setText(f"한 번에 처리: {'  →  '.join(parts)}<br><small>{hint}</small>")
        self._flow_stop.show()
        self._flow_close.hide()
        self._flow_bar.show()

    def _continue_flow(self, page: str, folders: list) -> None:
        """한 번에 처리의 다음 단계: 그 화면에 폴더를 넣고 미리보기까지(실행은 사용자가 확인한 뒤)."""
        self._navigate(page, folders, replace=True)
        if self._current_page == page:
            QTimer.singleShot(0, self._run_current_preview)

    def _current_page_widget(self):
        if self._current_page in FileOrganizeTab.PAGES:
            return self._file_organize_tab.page_widget(self._current_page)
        return self._photo_selection_tab._best_cut_tab

    def _run_current_preview(self) -> None:
        """지금 화면의 "미리보기"를 누른다(파일을 바꾸지 않는 단계만 — 실행·복사 버튼은 누르지 않음)."""
        button = getattr(self._current_page_widget(), "_preview_button", None)
        if button is not None and button.isEnabled() and button.isVisible():
            button.click()

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

    def _navigate(self, page: str, folders: list, replace: bool = True) -> None:
        """다음 단계 버튼·최근 폴더: page 화면에 folders를 넣고 그 화면으로 간다(replace가 아니면
        여러 폴더를 받는 화면에서는 기존 목록에 더한다)."""
        if page in FileOrganizeTab.PAGES:
            target = self._file_organize_tab.page_widget(page)
        else:
            target = self._photo_selection_tab
        if target.is_busy:
            QMessageBox.warning(self, "작업 진행 중", "넘어갈 화면에서 작업이 진행 중입니다. 끝난 뒤 다시 눌러 주세요.")
            return
        target.receive_folders(folders, replace=replace)
        self._select_page(page)

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
        self._current_page = page.key

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
    apply_saved(app, make_settings())  # 보기 › 글자 크기·테마
    try:
        history.enable()  # 파일 작업 기록(파일 › 작업 기록)
    except OSError:
        pass
    window = MainWindow()
    app_log.install_excepthooks(on_error=window.show_unexpected_error)
    window.show()
    if should_show_welcome():
        window.show_welcome()
    window.updates.check_on_startup()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
