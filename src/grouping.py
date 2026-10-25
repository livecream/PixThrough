"""M2: 장면 그룹핑 (촬영 시각 간격 기준 + 선택적으로 이미지 유사도로 나누기).

PROJECT_SPEC.md 4.2절, 8절: 연속 촬영 시각 간격이 gap_seconds 이내인 사진들을
같은 장면으로 묶는다. 입력은 scan_and_extract가 반환하는, 이미 촬영 시각순으로
정렬된 목록을 전제로 한다.

2차 보강(사용자 선택): 시간상 같은 장면이어도 바로 앞 사진과 많이 다르면(카메라를
다른 피사체로 돌린 경우) 새 장면으로 나눈다. 비슷한데 시간이 떨어진 장면을 합치지는
않는다. 얼마나 다르면 나눌지는 사용자가 정하고(기본은 끔), 코드에 확정된 기본값은
없다 — CLAUDE.md "임의의 장면 판단 기준 확정 금지".
"""

from __future__ import annotations

from pathlib import Path

from image_hash import hash_distance, hashes_for
from scanner import PhotoMetadata

DEFAULT_SCENE_GAP_SECONDS = 3


def group_by_time_gap(
    photos: list[PhotoMetadata],
    gap_seconds: float = DEFAULT_SCENE_GAP_SECONDS,
) -> list[list[PhotoMetadata]]:
    """촬영 시각순으로 정렬된 사진들을 시간 간격 기준으로 장면 그룹으로 묶는다.

    연속한 두 사진의 촬영 시각 차이가 gap_seconds 이내(포함)면 같은 장면으로 묶고,
    초과하면 새 장면을 시작한다.
    """
    if not photos:
        return []

    groups: list[list[PhotoMetadata]] = [[photos[0]]]
    for previous, current in zip(photos, photos[1:]):
        gap = (current.datetime_original - previous.datetime_original).total_seconds()
        if gap <= gap_seconds:
            groups[-1].append(current)
        else:
            groups.append([current])
    return groups


def split_dissimilar(
    groups: list[list[PhotoMetadata]],
    hashes: dict[Path, int | None],
    max_distance: int,
) -> list[list[PhotoMetadata]]:
    """각 장면 안에서 바로 앞 사진과 지문 차이가 max_distance를 넘으면 그 자리에서
    새 장면을 시작한다. 지문이 없는(열 수 없는) 사진 앞뒤로는 나누지 않는다."""
    result: list[list[PhotoMetadata]] = []
    for group in groups:
        current = [group[0]]
        for previous, photo in zip(group, group[1:]):
            a, b = hashes.get(previous.path), hashes.get(photo.path)
            if a is not None and b is not None and hash_distance(a, b) > max_distance:
                result.append(current)
                current = [photo]
            else:
                current.append(photo)
        result.append(current)
    return result


def distances_to_previous(
    photos: list[PhotoMetadata], hashes: dict[Path, int | None]
) -> dict[Path, int | None]:
    """촬영 시각순으로 바로 앞 사진과의 지문 차이. 첫 사진이나 지문이 없으면 None.
    사용자가 나누기 기준을 정할 때 참고하도록 화면에 보여준다."""
    result: dict[Path, int | None] = {}
    previous_hash: int | None = None
    for photo in photos:
        current = hashes.get(photo.path)
        result[photo.path] = (
            hash_distance(previous_hash, current)
            if previous_hash is not None and current is not None
            else None
        )
        previous_hash = current
    return result


def group_scenes(
    photos: list[PhotoMetadata],
    gap_seconds: float = DEFAULT_SCENE_GAP_SECONDS,
    max_hash_distance: int | None = None,
    hash_cache: dict | None = None,
) -> list[list[PhotoMetadata]]:
    """시간 간격으로 묶고, max_hash_distance가 주어지면 비슷하지 않은 곳에서 더 나눈다."""
    groups = group_by_time_gap(photos, gap_seconds=gap_seconds)
    if max_hash_distance is None:
        return groups
    hashes = hashes_for([p.path for p in photos], cache=hash_cache)
    return split_dissimilar(groups, hashes, max_hash_distance)
