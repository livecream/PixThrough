from pathlib import Path

import disk_space
from file_ops import selection_bytes
from gui_format import space_warning
from resize import ACTION_COPY, ACTION_ERROR, ACTION_RESIZE, ResizePlanRow, estimate_output_bytes


def test_check_uses_nearest_existing_parent(tmp_path):
    check = disk_space.check(tmp_path / "not" / "yet", 10)
    assert check is not None and check.free > 0 and check.enough
    assert disk_space.same_drive(tmp_path, tmp_path / "x" / "y")


def test_space_warning_when_not_enough():
    assert space_warning(None, "x") == ""
    assert space_warning(disk_space.SpaceCheck(Path("."), 10, 10**12), "x") == ""
    text = space_warning(disk_space.SpaceCheck(Path("."), 5 * 1024**3, 1024**3), "베스트컷 폴더")
    assert "베스트컷 폴더" in text and "5.0GB" in text and "1.0GB" in text
    # 여유분(최소 50MB)까지 봐야 모자람
    assert not disk_space.SpaceCheck(Path("."), 100, 100 + 1024).enough


def test_selection_bytes_counts_matching_raw(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"x" * 10)
    (tmp_path / "a.CR3").write_bytes(b"x" * 100)
    (tmp_path / "b.NEF").write_bytes(b"x" * 7)
    assert selection_bytes([tmp_path / "a.jpg", tmp_path / "b.NEF", tmp_path / "gone.jpg"]) == 117


def test_resize_estimate(tmp_path):
    src = tmp_path / "s.jpg"
    src.write_bytes(b"x" * 1000)
    rows = [
        ResizePlanRow(src, tmp_path / "o.jpg", (4000, 3000), (2000, 1500), ACTION_RESIZE),
        ResizePlanRow(src, tmp_path / "o2.jpg", (100, 100), (100, 100), ACTION_COPY),
        ResizePlanRow(src, tmp_path / "o3.jpg", None, None, ACTION_ERROR, "x"),
    ]
    assert estimate_output_bytes(rows) == int(2000 * 1500 * 0.45) + 1000
