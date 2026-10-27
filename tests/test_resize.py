import json
import os

import pytest
from PIL import Image

from resize import (
    ACTION_CONVERT,
    ACTION_COPY,
    ACTION_ERROR,
    ACTION_RESIZE,
    apply_resize_plan,
    build_resize_plan,
    replan,
    target_size,
)


def _jpeg(path, size=(400, 300), exif_orientation=None, color=(200, 100, 50)):
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, color)
    exif = Image.Exif()
    exif[0x010F] = "TestCam"  # Make
    if exif_orientation is not None:
        exif[0x0112] = exif_orientation
    image.save(path, "JPEG", exif=exif.tobytes(), quality=95)
    return path


def test_target_size_keeps_ratio_and_never_enlarges():
    assert target_size(6000, 4000, 2048) == (2048, 1365)
    assert target_size(4000, 6000, 2048) == (1365, 2048)
    assert target_size(1000, 800, 2048) == (1000, 800)
    assert target_size(2048, 10, 2048) == (2048, 10)


def test_plan_lists_actions_and_skips_raw(tmp_path):
    src = tmp_path / "in"
    _jpeg(src / "big.jpg", (400, 300))
    _jpeg(src / "small.JPG", (100, 80))
    Image.new("RGBA", (120, 90), (0, 0, 255, 128)).save(src / "tiny.png")
    (src / "shot.CR3").write_bytes(b"raw")
    (src / "broken.jpg").write_bytes(b"not an image")

    plan = build_resize_plan(src, tmp_path / "out", 200)

    by_name = {row.source.name: row for row in plan.rows}
    assert by_name["big.jpg"].action == ACTION_RESIZE
    assert by_name["big.jpg"].new_size == (200, 150)
    assert by_name["small.JPG"].action == ACTION_COPY
    assert by_name["tiny.png"].action == ACTION_CONVERT
    assert by_name["tiny.png"].destination.name == "tiny.jpg"
    assert by_name["broken.jpg"].action == ACTION_ERROR
    assert plan.skipped_raw == 1
    assert not (tmp_path / "out").exists()  # 계획만, 파일은 아직 안 만듦


def test_plan_reports_displayed_size_for_rotated_photos(tmp_path):
    src = tmp_path / "in"
    _jpeg(src / "portrait.jpg", (400, 300), exif_orientation=6)

    row = build_resize_plan(src, tmp_path / "out", 200).rows[0]

    assert row.original_size == (300, 400)
    assert row.new_size == (150, 200)


def test_apply_resizes_keeps_exif_and_original_untouched(tmp_path):
    src = tmp_path / "in"
    original = _jpeg(src / "a.jpg", (400, 300), exif_orientation=6)
    os.utime(original, (1_600_000_000, 1_600_000_000))
    before = original.read_bytes()
    out = tmp_path / "out"
    plan = build_resize_plan(src, out, 200)

    results = apply_resize_plan(plan.rows, out, 200, 85)

    assert original.read_bytes() == before
    with Image.open(out / "a.jpg") as resized:
        assert resized.format == "JPEG"
        assert resized.size == (200, 150)  # 픽셀은 그대로 두고 회전 정보를 옮김
        exif = resized.getexif()
        assert exif[0x0112] == 6 and exif[0x010F] == "TestCam"
    assert (out / "a.jpg").stat().st_mtime == pytest.approx(1_600_000_000)
    assert [r.error for r in results] == [None]
    log = next(out.glob("resize_log_*.jsonl")).read_text(encoding="utf-8")
    assert json.loads(log.splitlines()[0])["destination"] == str(out / "a.jpg")


def test_apply_copies_small_jpeg_and_converts_png(tmp_path):
    src = tmp_path / "in"
    small = _jpeg(src / "small.jpg", (100, 80))
    Image.new("RGBA", (120, 90), (0, 0, 255, 0)).save(src / "clear.png")
    out = tmp_path / "out"
    plan = build_resize_plan(src, out, 200)

    apply_resize_plan(plan.rows, out, 200, 90)

    assert (out / "small.jpg").read_bytes() == small.read_bytes()
    with Image.open(out / "clear.jpg") as converted:
        assert converted.format == "JPEG" and converted.mode == "RGB"
        assert converted.getpixel((5, 5)) == (255, 255, 255)  # 투명 → 흰 배경


def test_recursive_keeps_subfolders_and_never_overwrites(tmp_path):
    src = tmp_path / "in"
    _jpeg(src / "a.jpg", (400, 300))
    _jpeg(src / "day2" / "a.jpg", (400, 300))
    Image.new("RGB", (400, 300)).save(src / "a.png")  # a.jpg와 이름이 겹침
    out = tmp_path / "out"
    out.mkdir()
    (out / "a.jpg").write_bytes(b"existing")

    plan = build_resize_plan(src, out, 200, recursive=True)
    apply_resize_plan(plan.rows, out, 200, 90)

    assert (out / "a.jpg").read_bytes() == b"existing"
    names = sorted(str(p.relative_to(out)) for p in out.rglob("*.jpg"))
    assert names == sorted(["a.jpg", "a (1).jpg", "a (2).jpg", os.path.join("day2", "a.jpg")])


def test_top_level_only_by_default(tmp_path):
    src = tmp_path / "in"
    _jpeg(src / "a.jpg")
    _jpeg(src / "sub" / "b.jpg")

    plan = build_resize_plan(src, tmp_path / "out", 200)

    assert [r.source.name for r in plan.rows] == ["a.jpg"]


@pytest.mark.parametrize("output", ["in", "in/out", "."])
def test_output_overlapping_input_is_refused(tmp_path, output):
    src = tmp_path / "in"
    _jpeg(src / "a.jpg")

    with pytest.raises(ValueError):
        build_resize_plan(src, tmp_path / output, 200)


def test_apply_continues_after_a_failure(tmp_path):
    src = tmp_path / "in"
    _jpeg(src / "a.jpg", (400, 300))
    bad = _jpeg(src / "b.jpg", (400, 300))
    out = tmp_path / "out"
    plan = build_resize_plan(src, out, 200)
    bad.write_bytes(b"broken after preview")

    results = apply_resize_plan(plan.rows, out, 200, 90)

    assert [(r.source.name, r.error is None) for r in results] == [("a.jpg", True), ("b.jpg", False)]
    assert not (out / "b.jpg").exists()


def test_replan_recomputes_without_reading_files(tmp_path):
    src = tmp_path / "in"
    _jpeg(src / "a.jpg", (400, 300))
    rows = build_resize_plan(src, tmp_path / "out", 200).rows
    (src / "a.jpg").unlink()  # 다시 읽지 않는다

    bigger = replan(rows, 500)

    assert bigger[0].action == ACTION_COPY and bigger[0].new_size == (400, 300)
    assert replan(bigger, 100)[0].new_size == (100, 75)
