import datetime
import json
import os

import piexif
import pytest
from PIL import Image

from organize import (
    MovePlanRow,
    build_capture_date_plan,
    build_move_by_extension_plan,
    build_undo_plan,
    delete_duplicate_files,
    find_duplicate_files,
    find_empty_folders,
    find_raw_jpg_mismatches,
    remove_empty_folders,
    summarize_folder,
    write_operation_log,
)
from organize import apply_move_plan


def _make_jpeg(path, dt=None, color=(200, 50, 50)):
    img = Image.new("RGB", (32, 24), color)
    if dt is not None:
        exif_dict = {
            "0th": {},
            "Exif": {
                piexif.ExifIFD.DateTimeOriginal: dt.strftime("%Y:%m:%d %H:%M:%S").encode(),
            },
            "1st": {},
            "thumbnail": None,
        }
        img.save(path, "jpeg", exif=piexif.dump(exif_dict))
    else:
        img.save(path, "jpeg")
    return path


def _make_video(path, dt):
    path.write_bytes(b"fake video bytes")
    ts = dt.timestamp()
    os.utime(path, (ts, ts))
    return path


def test_build_move_by_extension_plan_targets_top_level_only(tmp_path):
    raw = tmp_path / "a.cr3"
    raw.write_bytes(b"fake raw")
    _make_jpeg(tmp_path / "a.jpg")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.cr3").write_bytes(b"fake raw nested")

    rows = build_move_by_extension_plan(tmp_path, {".cr3"}, "RAW")

    assert [r.old_path.name for r in rows] == ["a.cr3"]
    assert rows[0].new_path == tmp_path / "RAW" / "a.cr3"


def test_apply_move_plan_moves_files_and_creates_dest_dir(tmp_path):
    raw = tmp_path / "a.cr3"
    raw.write_bytes(b"fake raw")
    rows = build_move_by_extension_plan(tmp_path, {".cr3"}, "RAW")

    apply_move_plan(rows)

    assert not raw.exists()
    assert (tmp_path / "RAW" / "a.cr3").exists()


def test_find_raw_jpg_mismatches_reports_both_directions(tmp_path):
    _make_jpeg(tmp_path / "matched.jpg")
    (tmp_path / "matched.cr3").write_bytes(b"fake raw")
    _make_jpeg(tmp_path / "orphan_photo.jpg")
    (tmp_path / "orphan_raw.cr3").write_bytes(b"fake raw")

    messages = find_raw_jpg_mismatches(tmp_path)

    assert any("orphan_photo.jpg" in m for m in messages)
    assert any("orphan_raw.cr3" in m for m in messages)
    assert not any("matched" in m for m in messages)


def test_find_raw_jpg_mismatches_empty_when_all_paired(tmp_path):
    _make_jpeg(tmp_path / "a.jpg")
    (tmp_path / "a.cr3").write_bytes(b"fake raw")

    assert find_raw_jpg_mismatches(tmp_path) == []


def test_build_capture_date_plan_groups_photos_and_videos_by_date(tmp_path):
    _make_jpeg(tmp_path / "a.jpg", datetime.datetime(2024, 1, 1, 10, 0, 0))
    _make_jpeg(tmp_path / "b.jpg", datetime.datetime(2024, 1, 2, 9, 0, 0))
    _make_video(tmp_path / "c.mp4", datetime.datetime(2024, 1, 1, 11, 0, 0))

    rows = build_capture_date_plan(tmp_path, video_extensions={".mp4"})
    by_name = {r.old_path.name: r.new_path for r in rows}

    assert by_name["a.jpg"] == tmp_path / "2024-01-01" / "a.jpg"
    assert by_name["b.jpg"] == tmp_path / "2024-01-02" / "b.jpg"
    assert by_name["c.mp4"] == tmp_path / "2024-01-01" / "c.mp4"


def test_build_capture_date_plan_ignores_unsupported_files(tmp_path):
    (tmp_path / "notes.txt").write_text("hello")

    assert build_capture_date_plan(tmp_path, video_extensions={".mp4"}) == []


def test_find_empty_folders_finds_nested_empty_dirs_deepest_first(tmp_path):
    (tmp_path / "keep").mkdir()
    _make_jpeg(tmp_path / "keep" / "a.jpg")

    empty_parent = tmp_path / "empty_parent"
    empty_child = empty_parent / "empty_child"
    empty_child.mkdir(parents=True)

    result = find_empty_folders(tmp_path)

    assert empty_child in result
    assert empty_parent in result
    assert tmp_path / "keep" not in result
    # 자식이 부모보다 먼저 나와야 순서대로 삭제해도 안전함
    assert result.index(empty_child) < result.index(empty_parent)


def test_find_empty_folders_ignores_folder_with_file_in_subtree(tmp_path):
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    _make_jpeg(nested / "photo.jpg")

    result = find_empty_folders(tmp_path)

    assert tmp_path / "a" not in result
    assert nested not in result


def test_remove_empty_folders_deletes_in_given_order(tmp_path):
    empty_parent = tmp_path / "empty_parent"
    empty_child = empty_parent / "empty_child"
    empty_child.mkdir(parents=True)

    remove_empty_folders([empty_child, empty_parent])

    assert not empty_parent.exists()


def test_remove_empty_folders_raises_if_folder_not_actually_empty(tmp_path):
    folder = tmp_path / "not_empty"
    folder.mkdir()
    (folder / "file.txt").write_text("oops")

    with pytest.raises(OSError):
        remove_empty_folders([folder])


def test_write_operation_log_records_move_rows(tmp_path):
    raw = tmp_path / "a.cr3"
    raw.write_bytes(b"fake raw")
    rows = build_move_by_extension_plan(tmp_path, {".cr3"}, "RAW")

    log_path = write_operation_log(rows, tmp_path, "move_raw")

    lines = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 1
    assert lines[0]["action"] == "move_raw"
    assert lines[0]["old_path"] == str(raw)
    assert lines[0]["new_path"] == str(tmp_path / "RAW" / "a.cr3")
    assert "timestamp" in lines[0]


def test_write_operation_log_records_removed_folders(tmp_path):
    folder = tmp_path / "empty"
    folder.mkdir()

    log_path = write_operation_log([folder], tmp_path, "remove_empty_folders")

    lines = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert lines[0]["action"] == "remove_empty_folders"
    assert lines[0]["path"] == str(folder)


def test_apply_move_plan_writes_log_when_log_folder_given(tmp_path):
    raw = tmp_path / "a.cr3"
    raw.write_bytes(b"fake raw")
    rows = build_move_by_extension_plan(tmp_path, {".cr3"}, "RAW")

    apply_move_plan(rows, log_folder=tmp_path, action="move_raw")

    logs = list(tmp_path.glob("organize_log_*.jsonl"))
    assert len(logs) == 1


def test_remove_empty_folders_writes_log_when_log_folder_given(tmp_path):
    folder = tmp_path / "empty"
    folder.mkdir()

    remove_empty_folders([folder], log_folder=tmp_path)

    logs = list(tmp_path.glob("organize_log_*.jsonl"))
    assert len(logs) == 1


def test_build_undo_plan_swaps_old_and_new(tmp_path):
    rows = [MovePlanRow(old_path=tmp_path / "a.cr3", new_path=tmp_path / "RAW" / "a.cr3")]

    undo_rows = build_undo_plan(rows)

    assert undo_rows[0].old_path == tmp_path / "RAW" / "a.cr3"
    assert undo_rows[0].new_path == tmp_path / "a.cr3"


def test_undo_plan_round_trip_moves_file_back(tmp_path):
    raw = tmp_path / "a.cr3"
    raw.write_bytes(b"fake raw")
    rows = [MovePlanRow(old_path=raw, new_path=tmp_path / "RAW" / "a.cr3")]

    apply_move_plan(rows)
    assert not raw.exists()

    apply_move_plan(build_undo_plan(rows))
    assert raw.exists()
    assert not (tmp_path / "RAW" / "a.cr3").exists()


def test_summarize_folder_counts_by_category_recursively(tmp_path):
    _make_jpeg(tmp_path / "a.jpg")
    (tmp_path / "a.cr3").write_bytes(b"1234")
    sub = tmp_path / "RAW"
    sub.mkdir()
    (sub / "b.cr3").write_bytes(b"12345678")
    _make_video(tmp_path / "c.mp4", datetime.datetime(2024, 1, 1))
    (tmp_path / "notes.txt").write_text("hi")

    summary = summarize_folder(tmp_path, video_extensions={".mp4"})

    assert summary.total_files == 5
    assert summary.photo_count == 1
    assert summary.raw_count == 2
    assert summary.video_count == 1
    assert summary.other_count == 1
    assert summary.total_size_bytes == sum(
        p.stat().st_size for p in tmp_path.rglob("*") if p.is_file()
    )


def test_summarize_folder_empty_folder_returns_zeros(tmp_path):
    summary = summarize_folder(tmp_path, video_extensions={".mp4"})

    assert summary.total_files == 0
    assert summary.total_size_bytes == 0


def test_find_duplicate_files_groups_identical_content(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"same content")
    (tmp_path / "b.jpg").write_bytes(b"same content")
    (tmp_path / "unique.jpg").write_bytes(b"different content")

    groups = find_duplicate_files(tmp_path)

    assert len(groups) == 1
    assert groups[0].keep == tmp_path / "a.jpg"
    assert groups[0].duplicates == [tmp_path / "b.jpg"]


def test_find_duplicate_files_ignores_same_size_different_content(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"aaaaaaaaaa")
    (tmp_path / "b.jpg").write_bytes(b"bbbbbbbbbb")

    assert find_duplicate_files(tmp_path) == []


def test_find_duplicate_files_searches_nested_folders(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"same content")
    sub = tmp_path / "RAW"
    sub.mkdir()
    (sub / "b.jpg").write_bytes(b"same content")

    groups = find_duplicate_files(tmp_path)

    assert len(groups) == 1
    assert set(groups[0].duplicates + [groups[0].keep]) == {tmp_path / "a.jpg", sub / "b.jpg"}


def test_find_duplicate_files_no_group_for_all_unique(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"one")
    (tmp_path / "b.jpg").write_bytes(b"two")

    assert find_duplicate_files(tmp_path) == []


def test_delete_duplicate_files_keeps_one_copy_per_group(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"same content")
    (tmp_path / "b.jpg").write_bytes(b"same content")
    groups = find_duplicate_files(tmp_path)

    deleted_count = delete_duplicate_files(groups)

    assert deleted_count == 1
    assert (tmp_path / "a.jpg").exists()
    assert not (tmp_path / "b.jpg").exists()


def test_delete_duplicate_files_writes_log_when_log_folder_given(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"same content")
    (tmp_path / "b.jpg").write_bytes(b"same content")
    groups = find_duplicate_files(tmp_path)

    delete_duplicate_files(groups, log_folder=tmp_path)

    logs = list(tmp_path.glob("organize_log_*.jsonl"))
    assert len(logs) == 1
    lines = [json.loads(line) for line in logs[0].read_text(encoding="utf-8").splitlines()]
    assert lines[0]["action"] == "delete_duplicate"
    assert lines[0]["path"] == str(tmp_path / "b.jpg")


def test_delete_duplicate_files_no_op_when_no_groups(tmp_path):
    assert delete_duplicate_files([]) == 0
    assert list(tmp_path.glob("organize_log_*.jsonl")) == []
