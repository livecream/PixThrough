"""앱 업데이트 화면: 시작할 때(설정으로 끌 수 있음)와 "도움말 > 업데이트 확인…"에서 새 버전을
확인하고, 받아서 교체한 뒤 다시 시작한다. 실제 작업은 updater.py가 한다.

- 시작할 때 확인은 조용히: 인터넷이 없거나 실패하면 아무것도 띄우지 않고, "이 버전 건너뛰기"한
  버전은 다시 묻지 않는다. 메뉴에서 직접 확인하면 결과(최신 버전입니다 / 실패 이유)를 보여준다.
- 사진 작업(미리보기·적용 등)이 진행 중이면 업데이트를 시작하지 않는다(앱이 꺼지므로).
- 스스로 교체할 수 없는 경우(소스 실행, 쓰기 권한 없음, macOS 임시 위치 실행)는 이유와 함께
  다운로드 페이지를 열어 준다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QProgressDialog, QWidget

import updater
from gui_settings import make_settings
from gui_worker import CancellableWorker
from version import __version__
import app_log

log = app_log.get("update")

SETTINGS_AUTO_CHECK = "update/check_on_startup"
SETTINGS_SKIPPED = "update/skipped_version"
STARTUP_CHECK_DELAY_MS = 2000


class CheckWorker(QThread):
    found = Signal(object)  # ReleaseInfo
    failed = Signal(str)

    def run(self) -> None:
        try:
            self.found.emit(updater.fetch_latest_release())
        except updater.UpdateError as e:
            self.failed.emit(str(e))


class DownloadWorker(CancellableWorker):
    progress = Signal(int, int)
    staged = Signal(object)  # Path
    failed = Signal(str)

    def __init__(self, release: updater.ReleaseInfo, target: updater.InstallTarget) -> None:
        super().__init__()
        self._release = release
        self._target = target

    def run(self) -> None:
        try:
            path = updater.download_and_stage(
                self._release,
                self._target,
                on_progress=lambda done, total: self.progress.emit(done, total),
                should_cancel=lambda: self._cancel_requested,
            )
        except updater.UpdateCancelled:
            self.cancelled.emit()
        except updater.UpdateError as e:
            self.failed.emit(str(e))
        else:
            self.staged.emit(path)


def _log_path() -> Path:
    import score_store  # 앱 데이터 폴더(모델·점수 저장 위치)와 같은 곳에 남긴다

    path = score_store.cache_dir().parent / "update.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


class UpdateController(QObject):
    """메인 창에 붙는 업데이트 담당. is_busy()는 사진 작업이 진행 중인지 알려준다."""

    def __init__(self, parent: QWidget, is_busy: Callable[[], bool]) -> None:
        super().__init__(parent)
        self._window = parent
        self._is_busy = is_busy
        self._settings = make_settings()
        self._check_worker: CheckWorker | None = None
        self._download_worker: DownloadWorker | None = None
        self._progress: QProgressDialog | None = None
        self._release: updater.ReleaseInfo | None = None
        self._target: updater.InstallTarget | None = None
        self._silent = True

    # --- 메뉴 ---------------------------------------------------------------
    def build_menu(self, menu: QMenu) -> None:
        check_action = QAction("업데이트 확인…", self._window)
        check_action.triggered.connect(lambda: self.check(silent=False))
        menu.addAction(check_action)

        auto_action = QAction("시작할 때 업데이트 확인", self._window)
        auto_action.setCheckable(True)
        auto_action.setChecked(self.auto_check_enabled())
        auto_action.toggled.connect(lambda on: self._settings.setValue(SETTINGS_AUTO_CHECK, on))
        menu.addAction(auto_action)

    def auto_check_enabled(self) -> bool:
        value = self._settings.value(SETTINGS_AUTO_CHECK, True)
        return value not in (False, "false", "0", 0)

    def check_on_startup(self) -> None:
        if self.auto_check_enabled():
            QTimer.singleShot(STARTUP_CHECK_DELAY_MS, lambda: self.check(silent=True))

    # --- 확인 ---------------------------------------------------------------
    def check(self, silent: bool) -> None:
        if self._check_worker is not None or self._download_worker is not None:
            return
        # 결과는 이 객체(메인 스레드)의 메서드로 받아야 대화상자가 메인 스레드에서 뜬다
        self._silent = silent
        worker = CheckWorker()
        worker.found.connect(self._on_found)
        worker.failed.connect(self._on_check_failed)
        worker.finished.connect(self._clear_check_worker)
        self._check_worker = worker
        worker.start()

    def _clear_check_worker(self) -> None:
        if self._check_worker is not None:
            self._check_worker.deleteLater()
        self._check_worker = None

    def _on_check_failed(self, message: str) -> None:
        log.warning("업데이트 확인 실패: %s", message)
        if not self._silent:
            QMessageBox.warning(self._window, "업데이트 확인", message)

    def _on_found(self, release: updater.ReleaseInfo) -> None:
        log.info("업데이트 확인: 최신 v%s (지금 v%s)", release.version, __version__)
        silent = self._silent
        if not updater.is_newer(release.version, __version__):
            if not silent:
                QMessageBox.information(
                    self._window, "업데이트 확인", f"최신 버전(v{__version__})을 쓰고 있습니다."
                )
            return
        if silent and self._settings.value(SETTINGS_SKIPPED, "") == release.version:
            return
        self._offer(release, silent)

    def _offer(self, release: updater.ReleaseInfo, silent: bool) -> None:
        target = updater.find_install_target()
        can_install = target.problem is None and release.asset_url is not None

        box = QMessageBox(self._window)
        box.setWindowTitle("새 버전")
        box.setIcon(QMessageBox.Icon.Information)
        text = f"새 버전 v{release.version}이 있습니다. (지금 v{__version__})"
        if can_install:
            info = (
                "업데이트를 누르면 새 버전을 받아 앱을 교체하고 다시 시작합니다. "
                "설정·마지막 폴더·저장된 점수는 그대로 남고, 사진은 건드리지 않습니다."
            )
        else:
            reason = target.problem or "이 운영체제용 파일이 릴리스에 없습니다."
            info = f"자동으로 교체할 수 없습니다: {reason}\n다운로드 페이지에서 직접 받아 주세요."
        box.setText(text)
        box.setInformativeText(info)
        if release.notes:
            box.setDetailedText(release.notes)
        primary = box.addButton(
            "업데이트" if can_install else "다운로드 페이지 열기", QMessageBox.ButtonRole.AcceptRole
        )
        skip = box.addButton("이 버전 건너뛰기", QMessageBox.ButtonRole.DestructiveRole) if silent else None
        box.addButton("나중에", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(primary)
        box.exec()

        clicked = box.clickedButton()
        if clicked is skip and skip is not None:
            self._settings.setValue(SETTINGS_SKIPPED, release.version)
        elif clicked is primary:
            if can_install:
                self._start_download(release, target)
            else:
                QDesktopServices.openUrl(QUrl(release.page_url or updater.RELEASES_PAGE))

    # --- 받기·교체 ------------------------------------------------------------
    def _start_download(self, release: updater.ReleaseInfo, target: updater.InstallTarget) -> None:
        if self._is_busy():
            QMessageBox.warning(
                self._window,
                "업데이트",
                "사진 작업이 진행 중입니다. 작업이 끝난 뒤 도움말 > 업데이트 확인…에서 다시 시도하세요.",
            )
            return
        self._release, self._target = release, target
        progress = QProgressDialog(f"v{release.version} 받는 중…", "취소", 0, 100, self._window)
        progress.setWindowTitle("업데이트")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)
        self._progress = progress

        worker = DownloadWorker(release, target)
        worker.progress.connect(self._on_progress)
        worker.staged.connect(self._on_staged)
        worker.failed.connect(self._on_download_failed)
        worker.cancelled.connect(self._close_progress)
        worker.finished.connect(self._clear_download_worker)
        progress.canceled.connect(worker.request_cancel)
        self._download_worker = worker
        worker.start()

    def _on_progress(self, done: int, total: int) -> None:
        # 창 모달 진행률 창은 setValue 안에서 이벤트를 처리해, 그 사이 다운로드 완료 처리가 창을
        # 닫을 수 있다 — 지역 변수로 잡아 두고 글자를 먼저 바꾼다.
        progress = self._progress
        if progress is None:
            return
        progress.setLabelText(
            f"v{self._release.version} 받는 중… {done / 1_000_000:.0f} / {total / 1_000_000:.0f} MB"
        )
        if total > 0:
            progress.setValue(min(100, done * 100 // total))

    def _close_progress(self) -> None:
        if self._progress is not None:
            self._progress.close()
            self._progress.deleteLater()
            self._progress = None

    def _clear_download_worker(self) -> None:
        if self._download_worker is not None:
            self._download_worker.deleteLater()
        self._download_worker = None

    def _on_download_failed(self, message: str) -> None:
        log.warning("업데이트 받기 실패: %s", message)
        self._close_progress()
        QMessageBox.warning(self._window, "업데이트 실패", f"{message}\n\n지금 버전은 그대로입니다.")

    def _on_staged(self, staged: Path) -> None:
        self._close_progress()
        if self._is_busy():
            QMessageBox.warning(
                self._window,
                "업데이트",
                "새 버전을 받았지만 사진 작업이 진행 중이라 교체하지 않았습니다. "
                "작업이 끝난 뒤 다시 업데이트하세요.",
            )
            return
        answer = QMessageBox.question(
            self._window,
            "업데이트",
            f"v{self._release.version}을 받았습니다. 앱을 닫고 새 버전으로 바꾼 뒤 다시 시작할까요?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            log.info("업데이트 교체 시작: v%s (%s)", self._release.version, staged)
            updater.launch_installer(self._target, staged, _log_path())
        except OSError as e:
            log.exception("업데이트 교체를 시작하지 못함")
            QMessageBox.warning(self._window, "업데이트 실패", f"교체를 시작하지 못했습니다: {e}")
            return
        QApplication.quit()
