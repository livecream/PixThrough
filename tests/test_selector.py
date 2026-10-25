import datetime

import numpy as np
import pytest
from PIL import Image, ImageFilter

from scanner import PhotoMetadata
from selector import _normalize, select_best


def _make_checkerboard(path, size=64, block=4):
    arr = (np.indices((size, size)).sum(axis=0) // block % 2 * 255).astype("uint8")
    Image.fromarray(arr).convert("RGB").save(path, "png")
    return path


def _make_blurred_checkerboard(path, size=64, block=4):
    arr = (np.indices((size, size)).sum(axis=0) // block % 2 * 255).astype("uint8")
    img = Image.fromarray(arr).convert("RGB").filter(ImageFilter.GaussianBlur(radius=5))
    img.save(path, "png")
    return path


def _make_flat(path, size=64, color=128):
    Image.new("RGB", (size, size), (color, color, color)).save(path, "png")
    return path


def _meta(path, seconds_offset=0):
    base = datetime.datetime(2026, 6, 1, 14, 0, 0)
    return PhotoMetadata(
        path=path,
        datetime_original=base + datetime.timedelta(seconds=seconds_offset),
        exif_source="exif",
    )


def test_normalize_scales_values_to_zero_one_range():
    assert _normalize([10.0, 20.0, 30.0]) == [0.0, 0.5, 1.0]


def test_normalize_returns_all_ones_when_all_values_equal():
    assert _normalize([5.0, 5.0, 5.0]) == [1.0, 1.0, 1.0]


def test_select_best_picks_the_sharper_photo(tmp_path):
    sharp_path = _make_checkerboard(tmp_path / "sharp.png")
    blurred_path = _make_blurred_checkerboard(tmp_path / "blurred.png")
    scene = [_meta(blurred_path, 0), _meta(sharp_path, 1)]

    selection = select_best(scene)

    assert selection.best.path == sharp_path


def test_select_best_breaks_ties_by_earliest_photo(tmp_path):
    p1 = _make_flat(tmp_path / "a.png")
    p2 = _make_flat(tmp_path / "b.png")
    scene = [_meta(p1, 0), _meta(p2, 1)]

    selection = select_best(scene)

    assert selection.best.path == p1


def test_select_best_raises_on_empty_scene():
    with pytest.raises(ValueError):
        select_best([])


def test_select_best_forwards_progress_callback(tmp_path):
    sharp_path = _make_checkerboard(tmp_path / "sharp.png")
    blurred_path = _make_blurred_checkerboard(tmp_path / "blurred.png")
    scene = [_meta(blurred_path, 0), _meta(sharp_path, 1)]
    calls = []

    select_best(scene, on_progress=lambda i, total: calls.append((i, total)))

    assert calls == [(1, 2), (2, 2)]


# --- 장면당 여러 장 남기기 (장면 크기에 비례, 올림, 최소 1장) -----------------

from selector import keep_count  # noqa: E402


@pytest.mark.parametrize(
    "size, percent, expected",
    [
        (5, 0, 1),     # 기본 0% → 장면마다 1장 (예전 동작)
        (5, 20, 1),    # 1.0 → 1
        (6, 20, 2),    # 1.2 → 올림 2
        (10, 20, 2),   # 정확히 2.0 → 2 (부동소수 오차로 3이 되지 않음)
        (11, 20, 3),
        (1, 50, 1),
        (3, 100, 3),   # 최대 장면 전체
        (3, 35, 2),    # 1.05 → 2
    ],
)
def test_keep_count_rounds_up_with_minimum_one(size, percent, expected):
    assert keep_count(size, percent) == expected


def test_select_best_keeps_top_photos_by_score_in_rank_order(tmp_path):
    sharp = _make_checkerboard(tmp_path / "sharp.png")
    medium = _make_checkerboard(tmp_path / "medium.png", block=8)
    blurred = _make_blurred_checkerboard(tmp_path / "blurred.png")
    flat_a = _make_flat(tmp_path / "flat_a.png")
    flat_b = _make_flat(tmp_path / "flat_b.png", color=120)
    scene = [_meta(p, i) for i, p in enumerate([flat_a, blurred, sharp, flat_b, medium])]

    selection = select_best(scene, keep_percent=40)  # 5장 × 40% = 2장

    kept = [scene[i].path for i in selection.kept_indices]
    assert kept[0] == sharp == selection.best.path
    assert len(kept) == 2
    assert [selection.combined_scores[i] for i in selection.kept_indices] == sorted(
        (selection.combined_scores[i] for i in selection.kept_indices), reverse=True
    )


def test_select_best_ties_keep_earlier_photos_first(tmp_path):
    paths = [_make_flat(tmp_path / f"{n}.png") for n in "abcd"]
    scene = [_meta(p, i) for i, p in enumerate(paths)]

    selection = select_best(scene, keep_percent=50)

    assert selection.kept_indices == [0, 1]


# --- 점수를 계산하지 못한 사진 ------------------------------------------------


def _broken(path):
    path.write_bytes(b"not an image")
    return path


def test_scene_of_only_broken_photos_selects_nothing(tmp_path):
    scene = [_meta(_broken(tmp_path / f"{n}.jpg"), i) for i, n in enumerate("ab")]

    selection = select_best(scene, keep_percent=100)

    assert selection.kept_indices == []
    assert selection.best is None and selection.best_index is None


def test_broken_photo_is_never_kept_and_does_not_skew_normalisation(tmp_path):
    sharp = _make_checkerboard(tmp_path / "sharp.png")
    blurred = _make_blurred_checkerboard(tmp_path / "blurred.png")
    broken = _broken(tmp_path / "broken.jpg")
    scene = [_meta(broken, 0), _meta(blurred, 1), _meta(sharp, 2)]

    selection = select_best(scene, keep_percent=100)

    assert selection.kept_indices == [2, 1]  # 손상 파일은 100%여도 남기지 않음
    # 흐린 사진이 정상 사진 중 가장 흐리므로 선명도 0으로 정규화된다(손상 파일의 0에 끌려가지 않음)
    with_broken = selection.combined_scores[1]
    without_broken = select_best([scene[1], scene[2]]).combined_scores[0]
    assert with_broken == pytest.approx(without_broken)
