"""내 선택에 맞춰보기: 사람이 최종으로 남긴 컷과 자동 1등 컷이 얼마나 맞는지(일치율)를 재고,
가중치를 바꿔 보며 더 잘 맞는 가중치를 추천한다. 추천만 하고, 적용은 사용자가 정한다.

사용자 결정(PROJECT_SPEC 8절):
- 일치 = 장면마다 그 가중치로 계산한 자동 1등 컷이 사람이 최종으로 남긴 컷(자동+직접 넣기/빼기)
  안에 있으면. 장면당 여러 장을 남겨도 자연스럽게 센다.
- 손대지 않은 장면도 "자동 결과를 보고 그대로 인정한 것"으로 센다 — 가중치를 바꿔 지금 맞는
  장면이 틀어지면 그만큼 일치율이 떨어진다(직접 고친 몇 장면에 치우친 추천을 막음).
- 남긴 컷이 없는 장면(전부 뺌)과 후보가 없는 장면(점수 실패·전부 C컷)은 셀 수 없어 뺀다.

선정 규칙은 selector와 같다: 선명도는 장면 안에서 점수를 낸 사진끼리 0~1로 정규화(C컷도 포함),
C컷·점수 실패 사진은 후보에서 빼고, 동점이면 먼저 찍은 컷.
"""

from __future__ import annotations

from dataclasses import dataclass

from report import ReportRow
from selector import DEFAULT_WEIGHTS, WEIGHT_KEYS, _normalize, combined_score

GRID_STEP = 5  # 가중치를 5% 단위로 훑는다(합 100)


@dataclass(frozen=True)
class _Photo:
    sharpness_norm: float
    exposure: float
    face: float | None
    smile: float | None
    candidate: bool  # C컷·점수 실패가 아니어서 자동으로 고를 수 있음
    kept: bool  # 사람이 최종으로 남김


@dataclass(frozen=True)
class _Scene:
    photos: tuple[_Photo, ...]
    touched: bool  # 직접 넣거나 뺀 사진이 있음


@dataclass(frozen=True)
class Agreement:
    matched: int
    total: int
    touched_matched: int
    touched_total: int

    @property
    def rate(self) -> float:
        return self.matched / self.total if self.total else 0.0


@dataclass(frozen=True)
class Candidate:
    weights: dict[str, float]
    agreement: Agreement


def _unscored(row: ReportRow) -> bool:
    return "점수 계산 실패" in row.reason


def build_scenes(rows: list[ReportRow]) -> list[_Scene]:
    """결과 줄(장면 순서)을 장면별로 묶어 셀 수 있는 장면만 돌려준다."""
    by_scene: dict[int, list[ReportRow]] = {}
    for row in rows:
        by_scene.setdefault(row.scene_id, []).append(row)
    scenes = []
    for members in by_scene.values():
        scored = [r for r in members if not _unscored(r)]
        if not scored:
            continue
        norms = dict(zip((id(r) for r in scored), _normalize([r.sharpness for r in scored])))
        photos = tuple(
            _Photo(
                norms[id(r)], r.exposure, r.face, r.smile,
                candidate=r.badcut_reason is None, kept=r.selected,
            )
            for r in scored
        )
        if not any(p.candidate for p in photos) or not any(r.selected for r in members):
            continue
        scenes.append(_Scene(photos, touched=any(r.manual_choice is not None for r in members)))
    return scenes


def _top_index(scene: _Scene, weights: dict[str, float]) -> int | None:
    best = None
    best_score = 0.0
    for i, photo in enumerate(scene.photos):  # 촬영 순서 — 동점이면 먼저 찍은 컷(첫 최대값)
        if not photo.candidate:
            continue
        score = combined_score(photo.sharpness_norm, photo.exposure, photo.face, weights, photo.smile)
        if best is None or score > best_score:
            best, best_score = i, score
    return best


def _top_is_kept(scene: _Scene, weights: dict[str, float]) -> bool:
    index = _top_index(scene, weights)
    return index is not None and scene.photos[index].kept


def agreement(scenes: list[_Scene], weights: dict[str, float]) -> Agreement:
    matched = touched_matched = touched_total = 0
    for scene in scenes:
        hit = _top_is_kept(scene, weights)
        matched += hit
        if scene.touched:
            touched_total += 1
            touched_matched += hit
    return Agreement(matched, len(scenes), touched_matched, touched_total)


def weight_grid(step: int = GRID_STEP, with_smile: bool = False) -> list[dict[str, float]]:
    """합이 100%인 (선명도, 노출, 눈뜸[, 미소]) 조합을 step% 단위로. 선명도·노출이 둘 다 0인 것은
    뺀다(얼굴이 없는 사진은 점수를 낼 수 없게 됨). with_smile이 아니면 미소는 0."""
    grid = []
    for s in range(0, 101, step):
        for e in range(0, 101 - s, step):
            if s == 0 and e == 0:
                continue
            rest = 100 - s - e
            for m in range(0, rest + 1, step) if with_smile else (0,):
                f = rest - m
                grid.append({"sharpness": s / 100, "exposure": e / 100, "face": f / 100, "smile": m / 100})
    return grid


def _distance(a: dict[str, float], b: dict[str, float]) -> float:
    return sum(abs(a.get(k, 0.0) - b.get(k, 0.0)) for k in WEIGHT_KEYS)


def recommend(
    rows: list[ReportRow], current: dict[str, float] | None = None, limit: int = 5
) -> tuple[Agreement, list[Candidate]]:
    """지금 가중치의 일치율과, 일치율이 높은 가중치 후보(같으면 지금 가중치에 가까운 순).
    고르는 컷이 똑같은 후보들(예: 얼굴이 없어 눈뜸 비중만 다른 것)은 지금에 가장 가까운 하나만."""
    current = current or DEFAULT_WEIGHTS
    scenes = build_scenes(rows)
    now = agreement(scenes, current)
    # 미소를 잰 사진이 있으면 미소 비중도 훑는다(칸이 하나 늘어 10% 단위로 — 너무 오래 걸리지 않게)
    with_smile = any(p.smile is not None for scene in scenes for p in scene.photos)
    grid = weight_grid(GRID_STEP * 2, with_smile=True) if with_smile else weight_grid()
    scored = [Candidate(w, agreement(scenes, w)) for w in grid]
    scored.sort(key=lambda c: (-c.agreement.matched, _distance(c.weights, current)))
    picked: list[Candidate] = []
    seen = {tuple(_top_index(scene, current) for scene in scenes)}  # 지금과 같은 결과는 추천하지 않음
    for candidate in scored:
        outcome = tuple(_top_index(scene, candidate.weights) for scene in scenes)
        if outcome in seen:
            continue
        seen.add(outcome)
        picked.append(candidate)
        if len(picked) == limit:
            break
    return now, picked
