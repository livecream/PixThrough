import json

import pytest
from PIL import Image

from file_ops import apply_selection, find_matching_raw, unique_destination
from report import ReportRow


def _make_flat(path, size=16, color=128):
    Image.new("RGB", (size, size), (color, color, color)).save(path, "png")
    return path


def _row(path, selected=True, scene_id=1):
    return ReportRow(
        path=path,
        scene_id=scene_id,
        sharpness=0.0,
        exposure=1.0,
        face=None,
        total=1.0,
        selected=selected,
        reason="선택됨: 장면 내 최고 종합점수" if selected else "제외: 종합 점수가 더 낮음",
    )


def test_unique_destination_returns_same_path_when_no_conflict(tmp_path):
    dest = unique_destination(tmp_path, "photo.jpg")

    assert dest == tmp_path / "photo.jpg"


def test_unique_destination_numbers_on_conflict(tmp_path):
    (tmp_path / "photo.jpg").write_bytes(b"existing")

    dest = unique_destination(tmp_path, "photo.jpg")

    assert dest == tmp_path / "photo (1).jpg"


def test_unique_destination_increments_past_multiple_conflicts(tmp_path):
    (tmp_path / "photo.jpg").write_bytes(b"existing")
    (tmp_path / "photo (1).jpg").write_bytes(b"existing")

    dest = unique_destination(tmp_path, "photo.jpg")

    assert dest == tmp_path / "photo (2).jpg"


def test_find_matching_raw_returns_sibling_with_same_stem(tmp_path):
    photo = _make_flat(tmp_path / "shot.jpg")
    raw = tmp_path / "shot.cr2"
    raw.write_bytes(b"fake raw")

    assert find_matching_raw(photo) == raw


def test_find_matching_raw_returns_none_when_absent(tmp_path):
    photo = _make_flat(tmp_path / "shot.jpg")

    assert find_matching_raw(photo) is None


def test_find_matching_raw_preserves_original_extension_case(tmp_path):
    photo = _make_flat(tmp_path / "shot.jpg")
    raw = tmp_path / "shot.CR3"
    raw.write_bytes(b"fake raw")

    found = find_matching_raw(photo)

    assert found is not None
    assert found.name == "shot.CR3"


def test_find_matching_raw_falls_back_to_raw_subfolder(tmp_path):
    photo = _make_flat(tmp_path / "shot.jpg")
    raw_dir = tmp_path / "RAW"
    raw_dir.mkdir()
    raw = raw_dir / "shot.cr2"
    raw.write_bytes(b"fake raw")

    assert find_matching_raw(photo) == raw


def test_find_matching_raw_prefers_same_dir_over_raw_subfolder(tmp_path):
    photo = _make_flat(tmp_path / "shot.jpg")
    same_dir_raw = tmp_path / "shot.cr2"
    same_dir_raw.write_bytes(b"same dir raw")
    raw_dir = tmp_path / "RAW"
    raw_dir.mkdir()
    (raw_dir / "shot.cr2").write_bytes(b"subfolder raw")

    assert find_matching_raw(photo) == same_dir_raw


def test_apply_selection_copies_selected_and_keeps_original(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    out_dir = tmp_path / "out"
    photo = _make_flat(src_dir / "a.jpg")
    rows = [_row(photo)]

    results = apply_selection(rows, out_dir)

    assert photo.exists()
    assert results[0].destination.exists()
    assert results[0].destination.parent == out_dir


def test_apply_selection_skips_unselected_rows(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    out_dir = tmp_path / "out"
    selected_photo = _make_flat(src_dir / "a.jpg")
    excluded_photo = _make_flat(src_dir / "b.jpg")
    rows = [_row(selected_photo, selected=True), _row(excluded_photo, selected=False)]

    results = apply_selection(rows, out_dir)

    assert len(results) == 1
    assert results[0].source == selected_photo


def test_apply_selection_move_removes_original(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    out_dir = tmp_path / "out"
    photo = _make_flat(src_dir / "a.jpg")
    rows = [_row(photo)]

    apply_selection(rows, out_dir, move=True)

    assert not photo.exists()


def test_apply_selection_copies_matching_raw_into_raw_subfolder(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    out_dir = tmp_path / "out"
    photo = _make_flat(src_dir / "a.jpg")
    raw = src_dir / "a.cr2"
    raw.write_bytes(b"fake raw")
    rows = [_row(photo)]

    results = apply_selection(rows, out_dir)

    assert results[0].raw_destination == out_dir / "RAW" / "a.cr2"
    assert (out_dir / "RAW" / "a.cr2").exists()
    assert raw.exists()


def test_apply_selection_does_not_overwrite_existing_destination(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "a.jpg").write_bytes(b"already here")
    photo = _make_flat(src_dir / "a.jpg")
    rows = [_row(photo)]

    results = apply_selection(rows, out_dir)

    assert results[0].destination == out_dir / "a (1).jpg"
    assert (out_dir / "a.jpg").read_bytes() == b"already here"


def test_apply_selection_writes_timestamped_log(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    out_dir = tmp_path / "out"
    photo = _make_flat(src_dir / "a.jpg")
    rows = [_row(photo)]

    apply_selection(rows, out_dir)

    log_files = list(out_dir.glob("apply_log_*.jsonl"))
    assert len(log_files) == 1
    entries = [json.loads(line) for line in log_files[0].read_text(encoding="utf-8").splitlines()]
    assert len(entries) == 1
    assert "timestamp" in entries[0]
    assert entries[0]["action"] == "copy"
    # 왜 이 사진을 옮겼는지(선택 사유)도 로그에 남는다
    assert entries[0]["reason"] == rows[0].reason


def test_apply_selection_reports_progress_per_file(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    out_dir = tmp_path / "out"
    photo_a = _make_flat(src_dir / "a.jpg")
    photo_b = _make_flat(src_dir / "b.jpg")
    rows = [_row(photo_a), _row(photo_b)]
    calls = []

    apply_selection(rows, out_dir, on_progress=lambda i, total: calls.append((i, total)))

    assert calls == [(1, 2), (2, 2)]


def test_apply_selection_reports_progress_even_when_a_file_fails(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    out_dir = tmp_path / "out"
    missing_photo = src_dir / "missing.jpg"  # never created on disk
    rows = [_row(missing_photo)]
    calls = []

    with pytest.raises(RuntimeError):
        apply_selection(
            rows, out_dir, on_progress=lambda i, total: calls.append((i, total))
        )

    assert calls == [(1, 1)]


def test_apply_selection_raises_when_output_dir_equals_input_dir(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    photo = _make_flat(src_dir / "a.jpg")
    original_bytes = photo.read_bytes()
    rows = [_row(photo)]

    with pytest.raises(ValueError):
        apply_selection(rows, src_dir)

    assert photo.exists()
    assert photo.read_bytes() == original_bytes
    # No stray "a (1).jpg" duplicate was created next to the original.
    assert sorted(p.name for p in src_dir.iterdir()) == ["a.jpg"]


def test_apply_selection_raises_when_output_dir_nested_inside_input_dir(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    photo = _make_flat(src_dir / "a.jpg")
    original_bytes = photo.read_bytes()
    nested_out_dir = src_dir / "보정대기"
    rows = [_row(photo)]

    with pytest.raises(ValueError):
        apply_selection(rows, nested_out_dir)

    assert photo.exists()
    assert photo.read_bytes() == original_bytes
    # The guard must fire before output_dir gets created, so no stray
    # "보정대기" directory is left inside the source folder.
    assert sorted(p.name for p in src_dir.iterdir()) == ["a.jpg"]
