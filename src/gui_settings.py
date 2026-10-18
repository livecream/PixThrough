"""탭·역할별로 마지막 사용 폴더를 QSettings에 저장/복원하는 공용 헬퍼.
존재하지 않는 경로(외장 드라이브 분리 등)는 조용히 걸러낸다.

QSettings는 1개짜리 문자열 리스트를 저장했다가 읽으면 리스트가 아니라
문자열 하나로 돌아오는 경우가 있어(백엔드에 따라 다름), load_folder_list가
그 경우를 문자열 1개짜리 리스트로 취급해 방어한다."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings

ORG_NAME = "PixThrough"
APP_NAME = "PixThrough"


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


def load_folder(settings: QSettings, key: str) -> Path | None:
    raw = settings.value(key, "")
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_dir() else None


def save_folder(settings: QSettings, key: str, folder: Path | None) -> None:
    settings.setValue(key, str(folder) if folder is not None else "")


def load_float(settings: QSettings, key: str, default: float) -> float:
    """숫자 설정값. 백엔드에 따라 문자열로 돌아오기도 하고, 값이 깨져 있으면 기본값."""
    raw = settings.value(key, default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def save_float(settings: QSettings, key: str, value: float) -> None:
    settings.setValue(key, float(value))
