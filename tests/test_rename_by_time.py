import datetime
import json
import os

import piexif
import pytest
from PIL import Image

from rename_by_time import (
    RenamePlanRow,
    apply_merge_plan,
    apply_rename_plan,
    build_merge_plan,
    build_rename_plan,
    write_rename_report,
)


def _make_video(path, dt):
    path.write_bytes(b"fake video bytes")
    ts = dt.timestamp()
    os.utime(path, (ts, ts))
    return path


def _make_jpeg(path, dt, color=(200, 50, 50)):
    img = Image.new("RGB", (32, 24), color)
    exif_dict = {
        "0th": {},
        "Exif": {
            piexif.ExifIFD.DateTimeOriginal: dt.strftime("%Y:%m:%d %H:%M:%S").encode(),
        },
        "1st": {},
        "thumbnail": None,
    }
    img.save(path, "jpeg", exif=piexif.dump(exif_dict))
    return path


def test_build_rename_plan_merges_and_sorts_multiple_folders(tmp_path):
    body_a = tmp_path / "body_a"
    body_b = tmp_path / "body_b"
    body_a.mkdir()
    body_b.mkdir()
    _make_jpeg(body_a / "DSC_001.jpg", datetime.datetime(2026, 1, 1, 10, 0, 0))
    _make_jpeg(body_b / "IMG_001.jpg", datetime.datetime(2026, 1, 1, 10, 0, 5))
    _make_jpeg(body_a / "DSC_002.jpg", datetime.datetime(2026, 1, 1, 10, 0, 10))

    rows = build_rename_plan([body_a, body_b])

    assert [r.old_path.name for r in rows] == ["DSC_001.jpg", "IMG_001.jpg", "DSC_002.jpg"]
    assert [r.new_path.name for r in rows] == ["00001.jpg", "00002.jpg", "00003.jpg"]
    assert rows[0].new_path.parent == body_a
    assert rows[1].new_path.parent == body_b


def test_build_rename_plan_tie_breaks_by_path_deterministically(tmp_path):
    same_time = datetime.datetime(2026, 1, 1, 10, 0, 0)
    _make_jpeg(tmp_path / "b.jpg", same_time)
    _make_jpeg(tmp_path / "a.jpg", same_time)

    rows = build_rename_plan([tmp_path])

    assert [r.old_path.name for r in rows] == ["a.jpg", "b.jpg"]


def test_build_rename_plan_pairs_matching_raw(tmp_path):
    _make_jpeg(tmp_path / "shot.jpg", datetime.datetime(2026, 1, 1, 10, 0, 0))
    raw = tmp_path / "shot.cr3"
    raw.write_bytes(b"fake raw")

    rows = build_rename_plan([tmp_path])

    assert rows[0].raw_old_path == raw
    assert rows[0].raw_new_path.name == "00001.cr3"


def test_write_rename_report_writes_json(tmp_path):
    _make_jpeg(tmp_path / "a.jpg", datetime.datetime(2026, 1, 1, 10, 0, 0))
    rows = build_rename_plan([tmp_path])
    report_path = tmp_path / "report.json"

    write_rename_report(rows, report_path)

    data = json.loads(report_path.read_text(encoding="utf-8"))
    assert data[0]["old_path"] == str(rows[0].old_path)
    assert data[0]["new_path"] == str(rows[0].new_path)


def test_apply_rename_plan_renames_in_place(tmp_path):
    photo = _make_jpeg(tmp_path / "a.jpg", datetime.datetime(2026, 1, 1, 10, 0, 0))
    original_bytes = photo.read_bytes()
    rows = build_rename_plan([tmp_path])

    apply_rename_plan(rows, log_dir=tmp_path)

    assert not photo.exists()
    new_path = tmp_path / "00001.jpg"
    assert new_path.exists()
    assert new_path.read_bytes() == original_bytes


def test_apply_rename_plan_renames_matching_raw_too(tmp_path):
    _make_jpeg(tmp_path / "shot.jpg", datetime.datetime(2026, 1, 1, 10, 0, 0))
    raw = tmp_path / "shot.cr3"
    raw.write_bytes(b"fake raw")
    rows = build_rename_plan([tmp_path])

    apply_rename_plan(rows, log_dir=tmp_path)

    assert not raw.exists()
    assert (tmp_path / "00001.cr3").read_bytes() == b"fake raw"


def test_apply_rename_plan_handles_name_swap_without_data_loss(tmp_path):
    # 시간순으로는 "00002.jpg"가 먼저(-> 00001), "00001.jpg"가 나중(-> 00002)이라
    # 목표 이름이 서로의 원래 이름과 겹치는 스왑 상황을 만든다.
    early = _make_jpeg(tmp_path / "00002.jpg", datetime.datetime(2026, 1, 1, 10, 0, 0), color=(200, 50, 50))
    late = _make_jpeg(tmp_path / "00001.jpg", datetime.datetime(2026, 1, 1, 10, 0, 5), color=(50, 50, 200))
    early_bytes = early.read_bytes()
    late_bytes = late.read_bytes()
    rows = build_rename_plan([tmp_path])

    apply_rename_plan(rows, log_dir=tmp_path)

    assert (tmp_path / "00001.jpg").read_bytes() == early_bytes
    assert (tmp_path / "00002.jpg").read_bytes() == late_bytes


def test_apply_rename_plan_raises_on_external_collision(tmp_path):
    photo = _make_jpeg(tmp_path / "a.jpg", datetime.datetime(2026, 1, 1, 10, 0, 0))
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    unrelated = other_dir / "00001.jpg"
    unrelated.write_bytes(b"not part of this batch")
    rows = [
        RenamePlanRow(
            seq=1,
            old_path=photo,
            new_path=unrelated,
            raw_old_path=None,
            raw_new_path=None,
            datetime_original=datetime.datetime(2026, 1, 1, 10, 0, 0),
        )
    ]

    with pytest.raises(ValueError):
        apply_rename_plan(rows, log_dir=tmp_path)

    assert photo.exists()
    assert unrelated.read_bytes() == b"not part of this batch"


def test_apply_rename_plan_writes_log(tmp_path):
    _make_jpeg(tmp_path / "a.jpg", datetime.datetime(2026, 1, 1, 10, 0, 0))
    rows = build_rename_plan([tmp_path])

    apply_rename_plan(rows, log_dir=tmp_path)

    log_files = list(tmp_path.glob("rename_log_*.jsonl"))
    assert len(log_files) == 1
    entries = [json.loads(line) for line in log_files[0].read_text(encoding="utf-8").splitlines()]
    assert len(entries) == 1
    assert entries[0]["new_path"] == str(tmp_path / "00001.jpg")


def test_apply_rename_plan_reports_progress(tmp_path):
    _make_jpeg(tmp_path / "a.jpg", datetime.datetime(2026, 1, 1, 10, 0, 0))
    _make_jpeg(tmp_path / "b.jpg", datetime.datetime(2026, 1, 1, 10, 0, 5))
    rows = build_rename_plan([tmp_path])
    calls = []

    apply_rename_plan(rows, log_dir=tmp_path, on_progress=lambda i, total: calls.append((i, total)))

    assert calls == [(1, 2), (2, 2)]


def test_build_merge_plan_points_new_paths_into_output_dir(tmp_path):
    body_a = tmp_path / "body_a"
    body_b = tmp_path / "body_b"
    body_a.mkdir()
    body_b.mkdir()
    out = tmp_path / "merged"
    _make_jpeg(body_a / "DSC_001.jpg", datetime.datetime(2026, 1, 1, 10, 0, 0))
    _make_jpeg(body_b / "IMG_001.jpg", datetime.datetime(2026, 1, 1, 10, 0, 5))

    rows = build_merge_plan([body_a, body_b], out)

    assert [r.new_path for r in rows] == [out / "00001.jpg", out / "00002.jpg"]


def test_build_merge_plan_puts_raw_in_output_raw_subfolder(tmp_path):
    body = tmp_path / "body"
    body.mkdir()
    out = tmp_path / "merged"
    _make_jpeg(body / "shot.jpg", datetime.datetime(2026, 1, 1, 10, 0, 0))
    (body / "shot.cr3").write_bytes(b"fake raw")

    rows = build_merge_plan([body], out)

    assert rows[0].raw_new_path == out / "RAW" / "00001.cr3"


def test_apply_merge_plan_moves_files_across_directories(tmp_path):
    body_a = tmp_path / "body_a"
    body_b = tmp_path / "body_b"
    body_a.mkdir()
    body_b.mkdir()
    out = tmp_path / "merged"
    photo_a = _make_jpeg(body_a / "a.jpg", datetime.datetime(2026, 1, 1, 10, 0, 0))
    photo_b = _make_jpeg(body_b / "b.jpg", datetime.datetime(2026, 1, 1, 10, 0, 5))
    a_bytes = photo_a.read_bytes()
    b_bytes = photo_b.read_bytes()
    rows = build_merge_plan([body_a, body_b], out)

    apply_merge_plan(rows, out)

    assert not photo_a.exists()
    assert not photo_b.exists()
    assert (out / "00001.jpg").read_bytes() == a_bytes
    assert (out / "00002.jpg").read_bytes() == b_bytes


def test_apply_merge_plan_moves_matching_raw_to_raw_subfolder(tmp_path):
    body = tmp_path / "body"
    body.mkdir()
    out = tmp_path / "merged"
    _make_jpeg(body / "shot.jpg", datetime.datetime(2026, 1, 1, 10, 0, 0))
    raw = body / "shot.cr3"
    raw.write_bytes(b"fake raw")
    rows = build_merge_plan([body], out)

    apply_merge_plan(rows, out)

    assert not raw.exists()
    assert (out / "RAW" / "00001.cr3").read_bytes() == b"fake raw"


def test_apply_merge_plan_raises_when_output_dir_is_an_input_dir(tmp_path):
    body = tmp_path / "body"
    body.mkdir()
    photo = _make_jpeg(body / "a.jpg", datetime.datetime(2026, 1, 1, 10, 0, 0))
    rows = build_merge_plan([body], body)

    with pytest.raises(ValueError):
        apply_merge_plan(rows, body)

    assert photo.exists()


def test_apply_merge_plan_raises_on_pre_existing_target(tmp_path):
    body = tmp_path / "body"
    body.mkdir()
    out = tmp_path / "merged"
    out.mkdir()
    (out / "00001.jpg").write_bytes(b"already here")
    photo = _make_jpeg(body / "a.jpg", datetime.datetime(2026, 1, 1, 10, 0, 0))
    rows = build_merge_plan([body], out)

    with pytest.raises(ValueError):
        apply_merge_plan(rows, out)

    assert photo.exists()
    assert (out / "00001.jpg").read_bytes() == b"already here"


def test_apply_merge_plan_writes_log_and_reports_progress(tmp_path):
    body = tmp_path / "body"
    body.mkdir()
    out = tmp_path / "merged"
    _make_jpeg(body / "a.jpg", datetime.datetime(2026, 1, 1, 10, 0, 0))
    _make_jpeg(body / "b.jpg", datetime.datetime(2026, 1, 1, 10, 0, 5))
    rows = build_merge_plan([body], out)
    calls = []

    apply_merge_plan(rows, out, on_progress=lambda i, total: calls.append((i, total)))

    assert calls == [(1, 2), (2, 2)]
    log_files = list(out.glob("rename_log_*.jsonl"))
    assert len(log_files) == 1


def test_build_rename_plan_respects_custom_digits(tmp_path):
    _make_jpeg(tmp_path / "a.jpg", datetime.datetime(2026, 1, 1, 10, 0, 0))

    rows = build_rename_plan([tmp_path], digits=3)

    assert rows[0].new_path.name == "001.jpg"


def test_build_rename_plan_includes_videos_sorted_with_photos(tmp_path):
    _make_jpeg(tmp_path / "photo.jpg", datetime.datetime(2026, 1, 1, 10, 0, 0))
    _make_video(tmp_path / "clip.mp4", datetime.datetime(2026, 1, 1, 10, 0, 5))

    rows = build_rename_plan([tmp_path])

    assert [r.old_path.name for r in rows] == ["photo.jpg", "clip.mp4"]
    assert [r.new_path.name for r in rows] == ["00001.jpg", "00002.mp4"]


def test_build_rename_plan_uses_mtime_for_video_datetime(tmp_path):
    dt = datetime.datetime(2026, 1, 1, 10, 0, 0)
    _make_video(tmp_path / "clip.mov", dt)

    rows = build_rename_plan([tmp_path])

    assert rows[0].datetime_original == dt


def test_build_rename_plan_does_not_pair_raw_with_video(tmp_path):
    _make_video(tmp_path / "clip.mov", datetime.datetime(2026, 1, 1, 10, 0, 0))
    # 우연히 같은 베이스 이름의 RAW가 있어도 영상과는 짝짓지 않는다.
    (tmp_path / "clip.cr2").write_bytes(b"fake raw")

    rows = build_rename_plan([tmp_path])

    assert rows[0].raw_old_path is None
    assert rows[0].raw_new_path is None


def test_apply_rename_plan_renames_video_files(tmp_path):
    video = _make_video(tmp_path / "clip.mp4", datetime.datetime(2026, 1, 1, 10, 0, 0))
    original_bytes = video.read_bytes()
    rows = build_rename_plan([tmp_path])

    apply_rename_plan(rows, log_dir=tmp_path)

    assert not video.exists()
    assert (tmp_path / "00001.mp4").read_bytes() == original_bytes


def test_body_clock_offset_reorders_and_is_reported(tmp_path):
    import datetime as _dt
    from PIL import Image

    def shot(folder, name, when, serial):
        folder.mkdir(exist_ok=True)
        exif = Image.Exif()
        exif[0x010F] = "Canon"
        exif[0x0110] = "Canon EOS R5"
        ifd = exif.get_ifd(0x8769)
        ifd[0x9003] = when.strftime("%Y:%m:%d %H:%M:%S")
        ifd[0xA431] = serial
        Image.new("RGB", (8, 8)).save(folder / name, "JPEG", exif=exif.tobytes())

    t0 = _dt.datetime(2026, 5, 1, 14, 0, 0)
    body_a, body_b = tmp_path / "A", tmp_path / "B"
    shot(body_a, "a1.jpg", t0, "AAA")
    shot(body_a, "a2.jpg", t0 + _dt.timedelta(seconds=10), "AAA")
    shot(body_b, "b1.jpg", t0 + _dt.timedelta(seconds=95), "BBB")  # B 시계가 90초 빠름

    plain = build_rename_plan([body_a, body_b])
    fixed = build_rename_plan([body_a, body_b], offsets={"Canon EOS R5 #BBB": -90})

    assert [r.old_path.name for r in plain] == ["a1.jpg", "a2.jpg", "b1.jpg"]
    assert [r.old_path.name for r in fixed] == ["a1.jpg", "b1.jpg", "a2.jpg"]
    assert fixed[1].clock_offset == -90 and fixed[0].clock_offset == 0
    assert fixed[1].datetime_original == t0 + _dt.timedelta(seconds=5)
