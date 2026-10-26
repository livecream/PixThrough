"""M10: C컷(흔들림/눈감음으로 못 쓰는 사진) 판정.

select_best(selector.py)는 장면 그룹 안에서 상대적으로 가장 좋은 1장을 고르는
반면, 이 모듈은 장면 그룹과 무관하게 스캔된 사진 각각의 절대 품질(선명도,
눈뜬 정도)이 사용자가 지정한 임계값에 못 미치면 C컷으로 판정한다.

CLAUDE.md 8절: "스펙에 명시되지 않은 임의의... 점수 가중치를 마음대로 확정하는
것" 금지 — 그래서 이 모듈은 임계값 기본값을 두지 않는다. 호출자(CLI/GUI)가
매번 명시적으로 넘겨야 한다.

스코어링(사진마다 ~100ms대, 장 수가 많으면 전체로는 분 단위)과 임계값 판정
(단순 비교라 즉시 끝남)을 일부러 분리해뒀다 — GUI에서 한 번 스코어링해둔 값을
캐싱해두면, 사용자가 임계값(민감도)을 이리저리 바꿔볼 때마다 사진을 다시 읽고
채점하지 않고 classify_badcut()만 다시 불러 즉시 재분류할 수 있다.

파일 이동 자체는 새로 구현하지 않고 file_ops.apply_selection을 그대로 쓴다.
ReportRow.selected=True인 행만 옮기는 로직에 RAW 짝짓기/충돌 가드/로그가 이미
다 구현돼 있으므로, C컷 판정 결과를 ReportRow로 만들기만 하면 된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from report import ReportRow
from scanner import PhotoMetadata
from scoring import PhotoScore, score_photos


@dataclass(frozen=True)
class BadCutThresholds:
    min_sharpness: float
    min_eyes_open: float  # 0~1, face가 있는 사진에만 적용. 낮을수록 눈이 더 감김.


def _reason_for(sharpness: float, face: float | None, thresholds: BadCutThresholds) -> tuple[bool, str]:
    reasons = []
    if sharpness < thresholds.min_sharpness:
        reasons.append("블러(선명도 낮음)")
    if face is not None and face < thresholds.min_eyes_open:
        reasons.append("눈감음")

    if reasons:
        return True, "C컷: " + ", ".join(reasons)
    return False, "정상 컷(임계값 이상)"


def badcut_reason(score: PhotoScore, thresholds: BadCutThresholds) -> str | None:
    """이 사진이 C컷이면 사유(예: "블러(선명도 낮음), 눈감음"), 아니면 None.
    점수 계산에 실패한 사진은 판단 근거가 없어 None. 기준이 0이면 아무것도 C컷이 아니다."""
    if score.error is not None:
        return None
    is_bad, reason = _reason_for(score.sharpness, score.face, thresholds)
    return reason.removeprefix("C컷: ") if is_bad else None


def score_for_badcut(
    photos: list[PhotoMetadata],
    on_progress: Callable[[int, int], None] | None = None,
    cache: dict | None = None,
) -> list[PhotoScore]:
    """C컷 판정에 필요한 점수만 한 번 계산한다 (비용이 큰 부분).

    반환값을 classify_badcut()에 여러 번 넘기면(임계값을 바꿔가며) 사진을
    다시 읽고 채점하지 않고 즉시 재분류할 수 있다. cache가 주어지면 이전
    미리보기에서 이미 스코어링한, 바뀌지 않은 파일은 다시 채점하지 않는다.
    """
    return score_photos(photos, on_progress=on_progress, cache=cache)


def classify_badcut(
    photos: list[PhotoMetadata],
    scores: list[PhotoScore],
    thresholds: BadCutThresholds,
) -> list[ReportRow]:
    """이미 계산된 점수를 임계값과 비교해 즉시 C컷 여부를 매긴다 (스코어링 없음).

    스코어링 자체가 실패한 파일(손상된 이미지 등)은 판단 근거가 없으므로 C컷으로
    자동 분류하지 않고 사용자가 직접 확인하도록 별도 사유로 남긴다.
    """
    rows: list[ReportRow] = []

    for meta, score in zip(photos, scores):
        if score.error is not None:
            rows.append(
                ReportRow(
                    path=meta.path,
                    scene_id=0,
                    sharpness=score.sharpness,
                    exposure=score.exposure,
                    face=score.face,
                    total=score.sharpness,
                    selected=False,
                    reason=f"판정 불가: {score.error}",
                )
            )
            continue

        is_bad_cut, reason = _reason_for(score.sharpness, score.face, thresholds)
        rows.append(
            ReportRow(
                path=meta.path,
                scene_id=0,
                sharpness=score.sharpness,
                exposure=score.exposure,
                face=score.face,
                total=score.sharpness,
                selected=is_bad_cut,
                reason=reason,
                sharpness_region=score.sharpness_region,
                sharpness_global=score.sharpness_global,
            )
        )

    return rows


def build_badcut_rows(
    photos: list[PhotoMetadata],
    thresholds: BadCutThresholds,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[ReportRow]:
    """스캔된 사진 전체를 장면 구분 없이 한 번에 스코어링+C컷 판정한다.

    CLI처럼 임계값을 한 번만 쓰고 끝나는 경우를 위한 편의 함수.
    임계값을 여러 번 바꿔가며 재판정하려면 score_for_badcut() + classify_badcut()을
    따로 쓰는 편이 스코어링을 반복하지 않아 더 빠르다.
    """
    scores = score_for_badcut(photos, on_progress=on_progress)
    return classify_badcut(photos, scores, thresholds)
