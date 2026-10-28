"""오류 창을 사람이 읽기 쉽게: 첫 줄(무엇이 안 됐는지) + 자주 나는 OS 오류의 우리말 설명과
할 일 + 긴 원래 내용은 "자세히" 칸에. 작업 스레드는 오류를 글자(str(e))로 넘기므로 글자에서
알아본다."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

# (글자 안에 이 중 하나가 있으면, 설명)
_HINTS: list[tuple[tuple[str, ...], str]] = [
    (
        ("No space left", "Errno 28", "WinError 112", "Disk quota"),
        "디스크 공간이 부족합니다. 저장할 곳의 공간을 비우거나 다른 드라이브를 고르세요.",
    ),
    (
        ("Read-only file system", "Errno 30"),
        "읽기 전용 드라이브입니다. SD 카드라면 옆의 잠금 스위치를 확인하세요.",
    ),
    (
        ("WinError 32", "being used by another process"),
        "다른 앱(라이트룸·탐색기 미리보기 등)이 파일을 쓰고 있습니다. 그 앱을 닫고 다시 시도하세요.",
    ),
    (
        ("Permission denied", "Errno 13", "WinError 5", "Operation not permitted", "Errno 1]"),
        "권한이 없어 읽거나 쓸 수 없습니다. 다른 폴더를 고르거나, macOS라면 시스템 설정 › 개인정보 보호 및 "
        "보안 › 파일 및 폴더(또는 전체 디스크 접근)에서 PixThrough를 허용하세요.",
    ),
    (
        ("File name too long", "Errno 36", "WinError 206"),
        "경로가 너무 깁니다. 더 짧은 이름의 폴더를 고르세요.",
    ),
    (
        ("No such file", "Errno 2]", "WinError 2]", "WinError 3]", "cannot find the path"),
        "파일이나 폴더를 찾을 수 없습니다. 미리보기 뒤에 옮겨졌거나 외장 드라이브가 분리됐을 수 있습니다 — "
        "다시 미리보기 해 주세요.",
    ),
]
_DETAIL_AFTER = 300  # 이보다 긴 내용은 "자세히" 칸으로


def explain(message: str) -> str | None:
    """오류 글자에 맞는 우리말 설명(없으면 None)."""
    for needles, hint in _HINTS:
        if any(needle in message for needle in needles):
            return hint
    return None


def split_message(message: str) -> tuple[str, str | None, str | None]:
    """(보여줄 첫 줄, 설명, 자세히 칸 내용)."""
    message = message.strip() or "알 수 없는 오류"
    lines = message.splitlines()
    head = lines[0]
    if len(head) > _DETAIL_AFTER:
        head = head[:_DETAIL_AFTER] + "…"
    details = message if len(lines) > 1 or len(message) > _DETAIL_AFTER else None
    return head, explain(message), details


def folders_overlap(a: Path, b: Path) -> bool:
    """같은 폴더이거나 한쪽이 다른 쪽 안에 있으면 True(원본과 결과가 섞이는 경우)."""
    try:
        a, b = Path(a).resolve(), Path(b).resolve()
    except OSError:
        return False
    return a == b or a in b.parents or b in a.parents


def overlap_warning(sources: list[Path], target: Path | None, what: str) -> str:
    """target이 원본 폴더 중 하나와 겹치면 미리 보여줄 경고(실행하면 어차피 막힌다)."""
    if target is None:
        return ""
    for source in sources:
        if folders_overlap(source, target):
            return (
                f"⚠ {what}가 원본 폴더({source.name})와 같거나 서로 안에 들어 있어 실행할 수 없습니다 — "
                "겹치지 않는 폴더를 고르세요."
            )
    return ""


def show_error(parent: QWidget | None, title: str, message: str) -> None:
    from PySide6.QtWidgets import QMessageBox  # 화면 없는 테스트 환경에서도 이 모듈을 읽게

    head, hint, details = split_message(message)
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Critical)
    box.setWindowTitle(title)
    box.setText(head)
    if hint:
        box.setInformativeText(hint)
    if details:
        box.setDetailedText(details)
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    box.exec()
