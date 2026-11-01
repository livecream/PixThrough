from pathlib import Path

from report import MANUAL_ADDED, MANUAL_REMOVED, ReportRow, build_report
from selector import DEFAULT_WEIGHTS, format_weights
from weight_tuning import agreement, build_scenes, recommend, weight_grid


def _row(name, scene, sharpness, exposure, face=None, selected=False, manual=None, badcut=None, reason="선택됨"):
    return ReportRow(
        path=Path(name), scene_id=scene, sharpness=sharpness, exposure=exposure, face=face, total=0.0,
        selected=selected, reason=reason, manual_choice=manual, badcut_reason=badcut,
    )


def test_agreement_counts_scenes_where_auto_top_is_among_kept():
    rows = [
        # 장면 1: 기본 가중치로는 a(선명)가 1등, 사람은 b(노출 좋음)를 남김 → 불일치
        _row("a", 1, 100, 0.2, manual=MANUAL_REMOVED),
        _row("b", 1, 90, 0.9, selected=True, manual=MANUAL_ADDED),
        # 장면 2: 손대지 않음, 자동 1등 c를 남김 → 일치
        _row("c", 2, 50, 0.8, selected=True),
        _row("d", 2, 10, 0.1),
        # 장면 3: 다 뺌 → 셀 수 없음
        _row("e", 3, 50, 0.5, manual=MANUAL_REMOVED),
    ]
    scenes = build_scenes(rows)
    assert len(scenes) == 2
    result = agreement(scenes, DEFAULT_WEIGHTS)
    assert (result.matched, result.total, result.touched_matched, result.touched_total) == (1, 2, 0, 1)


def test_recommend_prefers_weights_that_match_and_stay_close_to_current():
    rows = [
        _row("a", 1, 100, 0.2, manual=MANUAL_REMOVED),
        _row("b", 1, 90, 0.9, selected=True, manual=MANUAL_ADDED),
        _row("c", 2, 50, 0.8, selected=True),
        _row("d", 2, 10, 0.1),
    ]
    now, candidates = recommend(rows)
    assert now.matched == 1
    best = candidates[0]
    assert best.agreement.matched == 2
    assert best.weights["exposure"] > DEFAULT_WEIGHTS["exposure"]  # 노출 비중을 올려야 b가 1등
    # 같은 일치율 중에서는 지금 가중치(50·30·20)에 가장 가까운 것
    second_best_distance = [
        sum(abs(c.weights[k] - DEFAULT_WEIGHTS[k]) for k in DEFAULT_WEIGHTS)
        for c in candidates if c.agreement.matched == 2
    ]
    assert second_best_distance == sorted(second_best_distance)


def test_badcut_and_unscored_photos_are_never_auto_top():
    rows = [
        _row("blur", 1, 100, 1.0, badcut="블러"),  # C컷은 후보 아님(정규화에는 포함)
        _row("ok", 1, 50, 0.5, selected=True),
        _row("broken", 2, 0, 0, reason="제외: 점수 계산 실패 (x)"),
        _row("fine", 2, 10, 0.5, selected=True),
    ]
    result = agreement(build_scenes(rows), DEFAULT_WEIGHTS)
    assert (result.matched, result.total) == (2, 2)


def test_agreement_matches_selector_for_untouched_results(tmp_path):
    # 사람이 손대지 않은 build_report 결과는 그 가중치에서 일치율 100%여야 한다(선정 규칙이 같음)
    import datetime

    from scanner import PhotoMetadata
    from scoring import PhotoScore, _stat_key

    photos = []
    cache = {}
    for i, (s, e, f) in enumerate([(80, 0.5, 0.9), (60, 0.9, None), (70, 0.7, 0.2), (20, 0.4, None)]):
        path = tmp_path / f"p{i}.jpg"
        path.write_bytes(b"x")
        photos.append(PhotoMetadata(path, datetime.datetime(2026, 1, 1, 12, 0, i), "exif"))
        cache[path] = (*_stat_key(path), PhotoScore(path=path, sharpness=s, exposure=e, face=f, total=0.0))
    weights = {"sharpness": 0.2, "exposure": 0.6, "face": 0.2}
    rows = build_report([photos[:2], photos[2:]], cache=cache, weights=weights)
    assert agreement(build_scenes(rows), weights).rate == 1.0
    assert "가중치 선명도 20·노출 60·눈뜸 20" in rows[0].reason


def test_weight_grid_sums_to_one_and_skips_face_only():
    grid = weight_grid(25)
    assert all(abs(sum(w.values()) - 1) < 1e-9 for w in grid)
    assert {"sharpness": 0.0, "exposure": 0.0, "face": 1.0} not in grid
    assert format_weights(DEFAULT_WEIGHTS) == "선명도 50·노출 30·눈뜸 20"


def test_recommend_skips_candidates_that_pick_the_same_cuts():
    # 얼굴이 없으면 눈뜸 비중은 결과를 안 바꾼다 — 같은 결과는 하나만, 지금과 같은 결과는 추천 안 함
    rows = [
        _row("a", 1, 100, 0.2, manual=MANUAL_REMOVED),
        _row("b", 1, 90, 0.9, selected=True, manual=MANUAL_ADDED),
        _row("c", 2, 50, 0.8, selected=True),
        _row("d", 2, 10, 0.1),
    ]
    _, candidates = recommend(rows)
    outcomes = [c.agreement.matched for c in candidates]
    assert len(candidates) == len(set(map(str, (c.weights for c in candidates))))
    assert outcomes[0] == 2 and len(candidates) <= 2  # 결과가 다른 조합은 두 장면에서 많아야 몇 가지


def test_smile_weight_zero_keeps_old_results_and_can_be_recommended():
    from selector import combined_score

    assert combined_score(0.5, 0.5, 0.5, DEFAULT_WEIGHTS, smile=1.0) == combined_score(0.5, 0.5, 0.5, DEFAULT_WEIGHTS)
    # 사람이 웃는 컷(b)을 남긴 장면 — 미소 비중이 있어야 b가 1등
    rows = [
        _row("a", 1, 100, 0.5, face=0.9, manual=MANUAL_REMOVED),
        _row("b", 1, 95, 0.5, face=0.9, selected=True, manual=MANUAL_ADDED),
    ]
    rows = [r.__class__(**{**r.__dict__, "smile": s}) for r, s in zip(rows, (0.0, 0.9))]
    now, candidates = recommend(rows)
    assert now.matched == 0
    assert candidates and candidates[0].weights["smile"] > 0 and candidates[0].agreement.matched == 1
    assert "미소" in format_weights(candidates[0].weights)
