"""베스트컷 화면에서 직접 고른 것(선택에 넣기/빼기)을 앱을 꺼도 남기는 저장소.

수천 장을 몇 번에 나눠 고를 때, 앱을 다시 켜고 같은 사진을 미리보기하면 지난번에 직접
고른 것이 그대로 돌아오게 한다. 점수 저장소(score_store)와 같은 앱 데이터 폴더의
choices.json에 경로 → [수정 시각, 크기, 넣음(true)/뺌(false)]으로 둔다.

- 파일의 수정 시각이나 크기가 바뀌었으면(다른 사진으로 바뀜) 그 기록은 쓰지 않는다.
- 연결이 끊긴 외장 드라이브의 사진도 다시 꽂았을 때 쓰도록, 없는 파일이라고 지우지
  않는다. 대신 MAX_ENTRIES를 넘으면 오래 전에 고른 것부터 버린다.
- 저장에 실패해도(디스크 가득 등) 앱 동작에는 영향이 없다 — 다음에 다시 고르면 된다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import score_store

VERSION = 1
MAX_ENTRIES = 50_000

_entries: dict[str, list] | None = None


def _path() -> Path:
    return score_store.cache_dir() / "choices.json"


def _load() -> dict[str, list]:
    global _entries
    if _entries is None:
        _entries = dict(score_store._read(_path(), VERSION))
    return _entries


def _signature(path: Path) -> tuple[float, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime, stat.st_size


def _save() -> None:
    entries = _load()
    while len(entries) > MAX_ENTRIES:
        entries.pop(next(iter(entries)))
    try:
        score_store._write(_path(), VERSION, entries)
    except OSError:
        pass


def restore(paths: Iterable[Path]) -> dict[Path, bool]:
    """paths 중 지난번에 직접 고른 기록이 있고 파일이 그대로인 것의 선택 여부."""
    entries = _load()
    choices: dict[Path, bool] = {}
    for path in paths:
        entry = entries.get(str(path))
        if not (isinstance(entry, list) and len(entry) == 3 and isinstance(entry[2], bool)):
            continue
        if _signature(path) == (entry[0], entry[1]):
            choices[path] = entry[2]
    return choices


def sync(paths: Iterable[Path], choices: dict[Path, bool]) -> None:
    """paths(지금 화면의 사진)에 대해 저장된 기록을 choices와 같게 맞추고 저장한다 —
    choices에 있으면 기록하고, 없으면(자동 선택 그대로) 기록을 지운다."""
    entries = _load()
    for path in paths:
        key = str(path)
        entries.pop(key, None)  # 다시 넣어 가장 최근으로
        if path in choices:
            signature = _signature(path)
            if signature is not None:
                entries[key] = [signature[0], signature[1], choices[path]]
    _save()


def forget(paths: Iterable[Path]) -> None:
    """옮긴 사진처럼 더 이상 그 자리에 없는 사진의 기록을 지운다."""
    entries = _load()
    for path in paths:
        entries.pop(str(path), None)
    _save()


def clear_memory() -> None:
    """테스트용: 앱을 껐다 켠 것처럼 메모리의 기록을 버린다."""
    global _entries
    _entries = None
