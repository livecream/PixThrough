"""베스트컷 화면에서 사용자가 직접 한 것을 앱을 꺼도 남기는 저장소.

- 직접 고른 것(선택에 넣기/빼기): choices.json, 값은 넣음(true)/뺌(false)
- 장면 직접 고치기(여기서 나누기/앞 장면과 합치기): scene_edits.json, 값은 "split"/"join"
- 고객이 고른 사진(고객 셀렉 가져오기): client_picks.json, 값은 고객 메모(없으면 "") — 베스트컷
  화면에 "고객 선택"으로 표시만 한다

수천 장을 몇 번에 나눠 고를 때, 앱을 다시 켜고 같은 사진을 미리보기하면 지난번에 직접
한 것이 그대로 돌아오게 한다. 점수 저장소(score_store)와 같은 앱 데이터 폴더에
경로 → [수정 시각, 크기, 값]으로 둔다.

- 파일의 수정 시각이나 크기가 바뀌었으면(다른 사진으로 바뀜) 그 기록은 쓰지 않는다.
- 연결이 끊긴 외장 드라이브의 사진도 다시 꽂았을 때 쓰도록, 없는 파일이라고 지우지
  않는다. 대신 MAX_ENTRIES를 넘으면 오래 전에 한 것부터 버린다.
- 저장에 실패해도(디스크 가득 등) 앱 동작에는 영향이 없다 — 다음에 다시 하면 된다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable

import score_store

VERSION = 1
MAX_ENTRIES = 50_000


def _signature(path: Path) -> tuple[float, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime, stat.st_size


class PathRecordStore:
    """사진 경로마다 값 하나를 (수정 시각, 크기)와 함께 저장한다."""

    def __init__(self, filename: str, valid: Callable[[object], bool]) -> None:
        self._filename = filename
        self._valid = valid
        self._entries: dict[str, list] | None = None

    def _path(self) -> Path:
        return score_store.cache_dir() / self._filename

    def _load(self) -> dict[str, list]:
        if self._entries is None:
            self._entries = dict(score_store._read(self._path(), VERSION))
        return self._entries

    def _save(self) -> None:
        entries = self._load()
        while len(entries) > MAX_ENTRIES:
            entries.pop(next(iter(entries)))
        try:
            score_store._write(self._path(), VERSION, entries)
        except OSError:
            pass

    def restore(self, paths: Iterable[Path]) -> dict[Path, object]:
        """paths 중 기록이 있고 파일이 그대로인 것의 값."""
        entries = self._load()
        values: dict[Path, object] = {}
        for path in paths:
            entry = entries.get(str(path))
            if not (isinstance(entry, list) and len(entry) == 3 and self._valid(entry[2])):
                continue
            if _signature(path) == (entry[0], entry[1]):
                values[path] = entry[2]
        return values

    def sync(self, paths: Iterable[Path], values: dict[Path, object]) -> None:
        """paths(지금 화면의 사진)에 대해 저장된 기록을 values와 같게 맞추고 저장한다 —
        values에 있으면 기록하고, 없으면(자동 그대로) 기록을 지운다."""
        entries = self._load()
        for path in paths:
            key = str(path)
            entries.pop(key, None)  # 다시 넣어 가장 최근으로
            if path in values:
                signature = _signature(path)
                if signature is not None:
                    entries[key] = [signature[0], signature[1], values[path]]
        self._save()

    def forget(self, paths: Iterable[Path]) -> None:
        """옮긴 사진처럼 더 이상 그 자리에 없는 사진의 기록을 지운다."""
        entries = self._load()
        for path in paths:
            entries.pop(str(path), None)
        self._save()

    def clear_memory(self) -> None:
        """테스트용: 앱을 껐다 켠 것처럼 메모리의 기록을 버린다."""
        self._entries = None


choices = PathRecordStore("choices.json", lambda value: isinstance(value, bool))
scene_edits = PathRecordStore("scene_edits.json", lambda value: value in ("split", "join"))
client_picks = PathRecordStore("client_picks.json", lambda value: isinstance(value, str))


# 직접 고른 것(예전 함수 이름 그대로)
def restore(paths: Iterable[Path]) -> dict[Path, bool]:
    return choices.restore(paths)  # type: ignore[return-value]


def sync(paths: Iterable[Path], values: dict[Path, bool]) -> None:
    choices.sync(paths, values)


def forget(paths: Iterable[Path]) -> None:
    paths = list(paths)
    choices.forget(paths)
    scene_edits.forget(paths)
    client_picks.forget(paths)


def clear_memory() -> None:
    choices.clear_memory()
    scene_edits.clear_memory()
    client_picks.clear_memory()
