"""M4: 장면별 베스트 컷 선정.

PROJECT_SPEC.md 4.4절: 장면 그룹 내 최고 점수 사진 1장을 선정한다 (동점 시 처리 규칙 필요).

가중치(DEFAULT_WEIGHTS)는 4.3절 방법론("가중치는 초기값을 정하고, 실제 테스트 폴더
결과를 사람이 검토하며 조정")에 따른 초기값이다. 선명도는 촬영마다 절대 스케일이
달라 장면 그룹 내에서 min-max 정규화(0~1)한 뒤 비교하고, 노출/얼굴 점수는 이미
0~1 스케일이라 그대로 쓴다. 얼굴이 없는 사진(face=None)은 해당 항목을 계산에서
빼고 나머지 가중치로만 정규화한다 (풍경 사진 등, PROJECT_SPEC.md 4.3절).

동점 처리: 촬영 시각이 가장 이른 컷을 선택한다 (그룹은 이미 시간순 정렬돼 있으므로
Python max()가 동점 시 먼저 나온 항목을 유지하는 성질을 그대로 이용).

장면당 몇 장을 남길지(PROJECT_SPEC.md 4.4절 "상위 N장 옵션", 사용자와 결정): 장면 사진
수에 비례해 keep_percent%를 올림한 수만큼, 최소 1장, 최대 장면 전체. 기본 0%는 장면마다
1장(예전 동작 그대로)이다.

점수를 계산하지 못한 사진(손상 파일 등)은 고를 근거가 없으므로 순위에도, 선명도 정규화
에도 넣지 않는다. 장면 사진이 전부 그렇다면 그 장면에서는 아무것도 고르지 않는다
(사용자가 필요하면 미리보기에서 직접 넣을 수 있다).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from scanner import PhotoMetadata
from scoring import PhotoScore, score_photos

DEFAULT_WEIGHTS = {"sharpness": 0.5, "exposure": 0.3, "face": 0.2}


@dataclass(frozen=True)
class SceneSelection:
    scene: list[PhotoMetadata]
    scores: list[PhotoScore]
    combined_scores: list[float]
    # 점수를 낸 사진이 하나도 없는 장면이면 None
    best: PhotoMetadata | None
    best_index: int | None
    # 남길 컷의 장면 내 위치, 종합점수 높은 순(동점이면 먼저 찍은 순). [0]은 best_index.
    kept_indices: list[int]


def keep_count(scene_size: int, keep_percent: int) -> int:
    """장면에서 남길 장 수: 사진 수 × keep_percent%를 올림, 최소 1장, 최대 장면 전체.
    정수로 계산해 20% × 10장 = 정확히 2장처럼 부동소수 오차가 끼지 않게 한다."""
    wanted = (scene_size * keep_percent + 99) // 100
    return min(scene_size, max(1, wanted))


def _normalize(values: list[float]) -> list[float]:
    """값 목록을 0~1로 min-max 정규화한다. 전부 같으면 차이가 없다는 뜻으로 전부 1.0."""
    lo, hi = min(values), max(values)
    if hi == lo:
        return [1.0 for _ in values]
    return [(v - lo) / (hi - lo) for v in values]


def _combined_score(norm_sharpness: float, exposure: float, face: float | None) -> float:
    parts = {"sharpness": norm_sharpness, "exposure": exposure}
    if face is not None:
        parts["face"] = face

    weight_sum = sum(DEFAULT_WEIGHTS[k] for k in parts)
    return sum(DEFAULT_WEIGHTS[k] * v for k, v in parts.items()) / weight_sum


def select_best(
    scene: list[PhotoMetadata],
    on_progress: Callable[[int, int], None] | None = None,
    cache: dict | None = None,
    keep_percent: int = 0,
) -> SceneSelection:
    """장면 그룹에서 종합 점수가 높은 사진을 고른다 (기본 1장, keep_percent로 더 남김)."""
    if not scene:
        raise ValueError("빈 장면 그룹은 베스트 컷을 선정할 수 없습니다.")

    scores = score_photos(scene, on_progress=on_progress, cache=cache)
    valid = [i for i, s in enumerate(scores) if s.error is None]
    combined_scores = [0.0] * len(scene)
    if valid:
        norm_sharpness = _normalize([scores[i].sharpness for i in valid])
        for i, ns in zip(valid, norm_sharpness):
            combined_scores[i] = _combined_score(ns, scores[i].exposure, scores[i].face)

    # sorted는 안정 정렬이라 동점이면 원래 순서(촬영 시각순)가 유지된다.
    ranked = sorted(valid, key=lambda i: -combined_scores[i])
    # 남길 수는 장면 전체 크기 기준이지만, 점수를 낸 사진 수보다 많을 수는 없다.
    kept_indices = ranked[: keep_count(len(scene), keep_percent)] if ranked else []
    best_index = kept_indices[0] if kept_indices else None
    return SceneSelection(
        scene=scene,
        scores=scores,
        combined_scores=combined_scores,
        best=scene[best_index] if best_index is not None else None,
        best_index=best_index,
        kept_indices=kept_indices,
    )
