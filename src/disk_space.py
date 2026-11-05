"""복사·저장 전에 대상 드라이브에 남은 공간이 충분한지 본다(하다가 "공간 부족"으로 멈추지 않게).

대상 폴더가 아직 없으면 있는 가장 가까운 상위 폴더의 드라이브를 본다. 파일 시스템 정보를 못 읽으면
(네트워크 드라이브 등) 확인하지 않는다(None). 여유분(MARGIN)은 폴더·기록 파일·파일 시스템 블록
때문에 실제로 조금 더 쓰는 몫이다.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

MARGIN_RATIO = 0.02
MARGIN_MIN = 50 * 1024 * 1024


@dataclass(frozen=True)
class SpaceCheck:
    target: Path
    needed: int
    free: int

    @property
    def enough(self) -> bool:
        return self.free >= self.needed + max(MARGIN_MIN, int(self.needed * MARGIN_RATIO))


def free_space(target: Path) -> int | None:
    path = Path(target)
    while not path.exists():
        if path.parent == path:
            return None
        path = path.parent
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


def check(target: Path, needed: int) -> SpaceCheck | None:
    free = free_space(target)
    if free is None:
        return None
    return SpaceCheck(Path(target), needed, free)


def same_drive(a: Path, b: Path) -> bool:
    """두 폴더가 같은 드라이브(파일 시스템)인지 — 이동이 이름 바꾸기로 끝나 공간이 안 드는지."""
    def device(path: Path) -> int | None:
        path = Path(path)
        while not path.exists():
            if path.parent == path:
                return None
            path = path.parent
        try:
            return path.stat().st_dev
        except OSError:
            return None

    da, db = device(a), device(b)
    return da is not None and da == db
