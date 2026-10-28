"""사진 파일을 OS 쪽으로 넘기는 오른쪽 클릭 메뉴 항목: Finder/탐색기에서 위치 보기, 기본 앱으로
열기(사진·짝 RAW). 파일을 바꾸지 않는다."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMenu, QMessageBox, QWidget

from file_ops import find_matching_raw
from raw_preview import is_raw

FILE_MANAGER = "Finder" if sys.platform == "darwin" else "탐색기" if sys.platform == "win32" else "파일 관리자"


def reveal_command(path: Path) -> list[str] | None:
    """파일을 고른 채로 파일 관리자를 여는 명령. 리눅스는 없음(폴더만 연다)."""
    if sys.platform == "darwin":
        return ["open", "-R", str(path)]
    if sys.platform == "win32":
        return ["explorer", f"/select,{path}"]
    return None


def reveal_in_file_manager(path: Path) -> bool:
    command = reveal_command(path)
    if command is not None:
        try:
            subprocess.Popen(command)  # noqa: S603 - 고정된 OS 명령, 경로만 인자
            return True
        except OSError:
            pass
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))


def open_with_default_app(path: Path) -> bool:
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def add_file_actions(menu: QMenu, parent: QWidget, path: Path) -> None:
    """메뉴 끝에 "Finder에서 보기", "기본 앱으로 열기"(+ 짝 RAW 열기)를 붙인다."""
    def fail(what: str) -> None:
        QMessageBox.warning(parent, "열기 실패", f"{what}을(를) 열지 못했습니다:\n{path}")

    menu.addSeparator()
    menu.addAction(
        f"{FILE_MANAGER}에서 보기",
        lambda: reveal_in_file_manager(path) or fail(FILE_MANAGER),
    )
    menu.addAction("기본 앱으로 열기", lambda: open_with_default_app(path) or fail("파일"))
    raw = None if is_raw(path) else find_matching_raw(path)
    if raw is not None:
        menu.addAction(
            f"RAW 열기 ({raw.suffix.lstrip('.').upper()})",
            lambda: open_with_default_app(raw) or fail("RAW"),
        )
