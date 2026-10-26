from pathlib import Path

from PIL import Image

import badcut
from badcut import BadCutThresholds, build_badcut_rows, classify_badcut, score_for_badcut
from file_ops import apply_selection
from scanner import PhotoMetadata
from scoring import PhotoScore


def _make_flat(path, size=16, color=128):
    Image.new("RGB", (size, size), (color, color, color)).save(path, "png")
    return path


def _meta(path):
    return PhotoMetadata(path=path, datetime_original=None, exif_source="exif")


def _stub_scores(monkeypatch, scores: list[PhotoScore]):
    calls = {}

    def fake_score_photos(photos, on_progress=None, cache=None):
        calls["photos"] = photos
        calls["on_progress"] = on_progress
        calls["cache"] = cache
        if on_progress is not None:
            for i in range(1, len(photos) + 1):
                on_progress(i, len(photos))
        return scores

    monkeypatch.setattr(badcut, "score_photos", fake_score_photos)
    return calls


def test_low_sharpness_is_flagged_as_bad_cut(tmp_path, monkeypatch):
    path = _make_flat(tmp_path / "a.png")
    _stub_scores(
        monkeypatch,
        [PhotoScore(path=path, sharpness=5.0, exposure=1.0, face=None, total=5.0)],
    )
    thresholds = BadCutThresholds(min_sharpness=10.0, min_eyes_open=0.3)

    rows = build_badcut_rows([_meta(path)], thresholds)

    assert rows[0].selected is True
    assert "블러" in rows[0].reason


def test_sharp_photo_without_face_is_not_flagged(tmp_path, monkeypatch):
    path = _make_flat(tmp_path / "a.png")
    _stub_scores(
        monkeypatch,
        [PhotoScore(path=path, sharpness=50.0, exposure=1.0, face=None, total=50.0)],
    )
    thresholds = BadCutThresholds(min_sharpness=10.0, min_eyes_open=0.3)

    rows = build_badcut_rows([_meta(path)], thresholds)

    assert rows[0].selected is False


def test_closed_eyes_below_threshold_is_flagged(tmp_path, monkeypatch):
    path = _make_flat(tmp_path / "a.png")
    _stub_scores(
        monkeypatch,
        [PhotoScore(path=path, sharpness=50.0, exposure=1.0, face=0.1, total=50.0)],
    )
    thresholds = BadCutThresholds(min_sharpness=10.0, min_eyes_open=0.3)

    rows = build_badcut_rows([_meta(path)], thresholds)

    assert rows[0].selected is True
    assert "눈감음" in rows[0].reason


def test_open_eyes_above_threshold_is_not_flagged(tmp_path, monkeypatch):
    path = _make_flat(tmp_path / "a.png")
    _stub_scores(
        monkeypatch,
        [PhotoScore(path=path, sharpness=50.0, exposure=1.0, face=0.9, total=50.0)],
    )
    thresholds = BadCutThresholds(min_sharpness=10.0, min_eyes_open=0.3)

    rows = build_badcut_rows([_meta(path)], thresholds)

    assert rows[0].selected is False


def test_scoring_errors_are_not_auto_flagged_as_bad_cut(tmp_path, monkeypatch):
    path = tmp_path / "broken.png"
    path.write_bytes(b"not a real image")
    _stub_scores(
        monkeypatch,
        [
            PhotoScore(
                path=path, sharpness=0.0, exposure=0.0, face=None, total=0.0,
                error="이미지를 열 수 없음",
            )
        ],
    )
    thresholds = BadCutThresholds(min_sharpness=10.0, min_eyes_open=0.3)

    rows = build_badcut_rows([_meta(path)], thresholds)

    assert rows[0].selected is False
    assert rows[0].reason.startswith("판정 불가")


def test_forwards_progress_callback(tmp_path, monkeypatch):
    path = _make_flat(tmp_path / "a.png")
    _stub_scores(
        monkeypatch,
        [PhotoScore(path=path, sharpness=50.0, exposure=1.0, face=None, total=50.0)],
    )
    thresholds = BadCutThresholds(min_sharpness=10.0, min_eyes_open=0.3)
    calls = []

    build_badcut_rows([_meta(path)], thresholds, on_progress=lambda i, total: calls.append((i, total)))

    assert calls == [(1, 1)]


def test_build_badcut_rows_works_with_real_scoring(tmp_path):
    # 모킹 없이 실제 scoring 경로가 잘 연결되는지 확인 (선명도 0인 단색 이미지).
    path = _make_flat(tmp_path / "flat.png")
    thresholds = BadCutThresholds(min_sharpness=1.0, min_eyes_open=0.3)

    rows = build_badcut_rows([_meta(path)], thresholds)

    assert rows[0].sharpness == 0.0
    assert rows[0].selected is True


def test_badcut_rows_move_flagged_photos_via_apply_selection(tmp_path, monkeypatch):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    out_dir = tmp_path / "out"
    bad_photo = _make_flat(src_dir / "blurry.png")
    good_photo = _make_flat(src_dir / "sharp.png")
    raw = src_dir / "blurry.cr2"
    raw.write_bytes(b"fake raw")

    _stub_scores(
        monkeypatch,
        [
            PhotoScore(path=bad_photo, sharpness=1.0, exposure=1.0, face=None, total=1.0),
            PhotoScore(path=good_photo, sharpness=99.0, exposure=1.0, face=None, total=99.0),
        ],
    )
    thresholds = BadCutThresholds(min_sharpness=10.0, min_eyes_open=0.3)
    rows = build_badcut_rows([_meta(bad_photo), _meta(good_photo)], thresholds)

    results = apply_selection(rows, out_dir, move=True)

    assert len(results) == 1
    assert results[0].source == bad_photo
    assert not bad_photo.exists()
    assert good_photo.exists()  # 정상 컷은 그대로 남아있어야 함
    assert (out_dir / "blurry.png").exists()
    assert (out_dir / "RAW" / "blurry.cr2").exists()


def test_classify_badcut_reclassifies_without_rescoring(tmp_path, monkeypatch):
    path = _make_flat(tmp_path / "a.png")
    calls = _stub_scores(
        monkeypatch,
        [PhotoScore(path=path, sharpness=20.0, exposure=1.0, face=None, total=20.0)],
    )
    photos = [_meta(path)]

    scores = score_for_badcut(photos)
    assert "photos" in calls  # score_photos was actually invoked once

    loose_rows = classify_badcut(photos, scores, BadCutThresholds(min_sharpness=10.0, min_eyes_open=0.3))
    strict_rows = classify_badcut(photos, scores, BadCutThresholds(min_sharpness=30.0, min_eyes_open=0.3))

    assert loose_rows[0].selected is False  # 20.0 >= 10.0
    assert strict_rows[0].selected is True  # 20.0 < 30.0


def test_classify_badcut_does_not_call_score_photos(tmp_path, monkeypatch):
    path = _make_flat(tmp_path / "a.png")

    def fail_if_called(*args, **kwargs):
        raise AssertionError("classify_badcut must not re-score")

    monkeypatch.setattr(badcut, "score_photos", fail_if_called)
    scores = [PhotoScore(path=path, sharpness=5.0, exposure=1.0, face=None, total=5.0)]

    rows = classify_badcut([_meta(path)], scores, BadCutThresholds(min_sharpness=10.0, min_eyes_open=0.3))

    assert rows[0].selected is True


def test_build_badcut_rows_still_scores_once_via_split_functions(tmp_path, monkeypatch):
    path = _make_flat(tmp_path / "a.png")
    calls = _stub_scores(
        monkeypatch,
        [PhotoScore(path=path, sharpness=5.0, exposure=1.0, face=None, total=5.0)],
    )

    rows = build_badcut_rows([_meta(path)], BadCutThresholds(min_sharpness=10.0, min_eyes_open=0.3))

    assert rows[0].selected is True
    assert "photos" in calls


def test_badcut_reason_lists_problems_or_none():
    thresholds = BadCutThresholds(min_sharpness=50.0, min_eyes_open=0.5)
    blurry = PhotoScore(path=Path("a.jpg"), sharpness=10.0, exposure=1.0, face=0.2, total=10.0)
    fine = PhotoScore(path=Path("b.jpg"), sharpness=90.0, exposure=1.0, face=None, total=90.0)
    broken = PhotoScore(path=Path("c.jpg"), sharpness=0.0, exposure=0.0, face=None, total=0.0, error="x")

    assert badcut.badcut_reason(blurry, thresholds) == "블러(선명도 낮음), 눈감음"
    assert badcut.badcut_reason(fine, thresholds) is None
    assert badcut.badcut_reason(broken, thresholds) is None
    assert badcut.badcut_reason(blurry, BadCutThresholds(0.0, 0.0)) is None  # 기준 0 = 끔
