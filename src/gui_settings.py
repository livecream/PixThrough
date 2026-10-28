"""탭·역할별로 마지막 사용 폴더를 QSettings에 저장/복원하는 공용 헬퍼.
존재하지 않는 경로(외장 드라이브 분리 등)는 조용히 걸러낸다.

QSettings는 1개짜리 문자열 리스트를 저장했다가 읽으면 리스트가 아니라
문자열 하나로 돌아오는 경우가 있어(백엔드에 따라 다름), load_folder_list가
그 경우를 문자열 1개짜리 리스트로 취급해 방어한다.

폴더를 저장할 때마다 화면과 상관없이 "최근 폴더"(RECENT_KEY, 최근 것부터 MAX_RECENT개)에도
남긴다 — 파일 › 최근 폴더 메뉴와, 폴더 고르기 창을 마지막 폴더에서 여는 데 쓴다."""

from __future__ import annotations

from pathlib import Path

from typing import TYPE_CHECKING

from PySide6.QtCore import QSettings

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

ORG_NAME = "PixThrough"
APP_NAME = "PixThrough"
RECENT_KEY = "recent/folders"
MAX_RECENT = 10


def make_settings() -> QSettings:
    return QSettings(ORG_NAME, APP_NAME)


def load_folder_list(settings: QSettings, key: str) -> list[Path]:
    raw = settings.value(key, [])
    if not raw:
        return []
    if isinstance(raw, str):
        raw = [raw]
    return [p for p in (Path(s) for s in raw) if p.is_dir()]


def save_folder_list(settings: QSettings, key: str, folders: list[Path]) -> None:
    settings.setValue(key, [str(p) for p in folders])
    for folder in reversed(folders):
        add_recent(settings, folder)


def load_folder(settings: QSettings, key: str) -> Path | None:
    raw = settings.value(key, "")
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_dir() else None


def save_folder(settings: QSettings, key: str, folder: Path | None) -> None:
    settings.setValue(key, str(folder) if folder is not None else "")
    if folder is not None:
        add_recent(settings, folder)


def add_recent(settings: QSettings, folder: Path) -> None:
    """최근 폴더 맨 앞에 넣는다(이미 있으면 앞으로 옮김)."""
    raw = settings.value(RECENT_KEY, [])
    if isinstance(raw, str):
        raw = [raw]
    entries = [str(folder)] + [entry for entry in (raw or []) if entry != str(folder)]
    settings.setValue(RECENT_KEY, entries[:MAX_RECENT])


def recent_folders(settings: QSettings) -> list[Path]:
    """최근 폴더(최근 것부터, 지금 없는 폴더는 뺌)."""
    return load_folder_list(settings, RECENT_KEY)


def clear_recent(settings: QSettings) -> None:
    settings.setValue(RECENT_KEY, [])


def pick_directory(parent: QWidget, title: str) -> str:
    """폴더 고르기 창을 가장 최근 폴더에서 연다(QFileDialog.getExistingDirectory와 같이 글자 반환,
    취소하면 빈 글자)."""
    from PySide6.QtWidgets import QFileDialog  # 화면 없는 테스트 환경(libEGL 없음)에서도 이 모듈을 읽게

    recent = recent_folders(make_settings())
    return QFileDialog.getExistingDirectory(parent, title, str(recent[0]) if recent else "")


def load_float(
    settings: QSettings, key: str, default: float, minimum: float, maximum: float
) -> float:
    """숫자 설정을 읽는다. 백엔드에 따라 문자열로 돌아오기도 해서 변환하고, 값이 없거나
    깨졌거나 범위를 벗어나면 기본값을 쓴다."""
    try:
        value = float(settings.value(key, default))
    except (TypeError, ValueError):
        return default
    if not minimum <= value <= maximum:
        return default
    return value


def save_float(settings: QSettings, key: str, value: float) -> None:
    settings.setValue(key, value)


def load_bool(settings: QSettings, key: str, default: bool) -> bool:
    """켜기/끄기 설정을 읽는다. ini 백엔드는 "true"/"false" 문자열로 돌려준다."""
    raw = settings.value(key, default)
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str) and raw.lower() in ("true", "false"):
        return raw.lower() == "true"
    return default


def save_bool(settings: QSettings, key: str, value: bool) -> None:
    settings.setValue(key, value)
