"""여러 촬영 건 몰아서 미리 채점(편의 3): 촬영 건 폴더마다 사진을 훑어 유사도 지문·품질 점수를 계산해
앱 저장소(score_store)에 넣어 둔다. 나중에 그 촬영 건을 열어 미리보기하면 저장된 점수를 써서 바로
결과가 나온다(밤새 돌려 두기). 사진 파일은 읽기만 하고, 촬영 건 하나가 끝날 때마다 저장한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import score_store
from image_hash import hashes_for
from scanner import scan_and_extract
from scoring import score_photos

STAGE_SCAN = "촬영 정보 읽는 중"
STAGE_HASH = "유사도 계산 중"
STAGE_SCORE = "채점 중"


@dataclass(frozen=True)
class FolderResult:
    folder: Path
    photos: int
    failed: int  # 점수를 못 낸 사진(손상 등)
    error: str | None = None  # 폴더 자체를 못 읽음


def prescore(
    folders: list[Path],
    on_progress: Callable[[int, int, str, int, int], None] | None = None,
) -> list[FolderResult]:
    """folders(하위 폴더 포함)를 차례로 채점한다. on_progress(폴더 번호, 폴더 수, 단계, 완료, 전체) —
    예외를 던지면(취소) 그때까지 끝낸 폴더의 점수는 저장돼 있다."""
    results = []
    score_cache, hash_cache = score_store.score_cache(), score_store.hash_cache()
    for number, folder in enumerate(folders, start=1):
        def step(stage: str):
            return lambda done, total, *_: on_progress and on_progress(number, len(folders), stage, done, total)

        try:
            photos = scan_and_extract(Path(folder), recursive=True, include_unpaired_raw=True,
                                      on_progress=step(STAGE_SCAN))
        except OSError as e:
            results.append(FolderResult(Path(folder), 0, 0, str(e)))
            continue
        try:
            hashes_for([p.path for p in photos], cache=hash_cache, on_progress=step(STAGE_HASH))
            scores = score_photos(photos, on_progress=step(STAGE_SCORE), cache=score_cache)
        finally:
            score_store.save()  # 취소해도 그때까지 계산한 것은 남긴다
        results.append(FolderResult(Path(folder), len(photos), sum(1 for s in scores if s.error)))
    return results
