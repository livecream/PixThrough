import datetime
from pathlib import Path

import pytest
from PIL import Image

import history_undo
from file_ops import apply_selection
from history import Entry
from organize import MovePlanRow, apply_move_plan
from rename_by_time import apply_rename_plan, build_rename_plan
from report import ReportRow


def _entry(kind, log_file):
    return Entry(datetime.datetime.now(), kind, "", None, Path(log_file))


def _photo(path, color=(10, 20, 30)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (16, 12), color).save(path, "JPEG")
    return path


def test_undo_badcut_move_with_raw(tmp_path):
    src = tmp_path / "in"
    a = _photo(src / "a.jpg")
    (src / "a.CR3").write_bytes(b"raw")
    b = _photo(src / "b.jpg", (200, 0, 0))
    rows = [ReportRow(a, 1, 1, 1, None, 1, True, "C컷"), ReportRow(b, 1, 1, 1, None, 1, False, "")]
    apply_selection(rows, tmp_path / "bad", move=True)
    log_file = next((tmp_path / "bad").glob("apply_log_*.jsonl"))
    entry = _entry("C컷 이동", log_file)
    assert history_undo.can_undo(entry)
    plan = history_undo.build_undo_plan(entry)
    assert len(plan.pairs) == 1 and not plan.problems
    result = history_undo.apply_undo(plan)
    assert len(result.restored) == 1 and not result.skipped
    assert a.exists() and (src / "a.CR3").read_bytes() == b"raw"
    assert not (tmp_path / "bad" / "a.jpg").exists() and not (tmp_path / "bad" / "RAW" / "a.CR3").exists()
    # 다시 하면 옮긴 파일이 없어 건너뜀
    again = history_undo.build_undo_plan(entry)
    assert again.ready == [] and "옮긴 파일이 없음" in again.problems[0][1]


def test_undo_organize_move_does_not_overwrite(tmp_path):
    folder = tmp_path / "f"
    raw = folder / "x.CR3"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"1")
    (folder / "y.CR3").write_bytes(b"2")
    apply_move_plan([MovePlanRow(raw, folder / "RAW" / "x.CR3"), MovePlanRow(folder / "y.CR3", folder / "RAW" / "y.CR3")],
                    log_folder=folder, action="move_raw")
    (folder / "y.CR3").write_bytes(b"new file")  # 원래 자리에 새 파일이 생김
    plan = history_undo.build_undo_plan(_entry("폴더 정리", next(folder.glob("organize_log_*.jsonl"))))
    assert [p.name for p, _ in plan.problems] == ["y.CR3"]
    result = history_undo.apply_undo(plan)
    assert raw.read_bytes() == b"1" and (folder / "y.CR3").read_bytes() == b"new file"
    assert (folder / "RAW" / "y.CR3").read_bytes() == b"2"
    assert len(result.restored) == 1 and len(result.skipped) == 1


def test_undo_in_place_rename_with_swapped_names(tmp_path):
    import piexif

    def shot(name, second):
        img = Image.new("RGB", (16, 12))
        exif = piexif.dump({"0th": {}, "Exif": {piexif.ExifIFD.DateTimeOriginal: f"2026:11:03 10:00:{second:02d}".encode()},
                            "1st": {}, "thumbnail": None})
        img.save(tmp_path / name, "JPEG", exif=exif)
        return (tmp_path / name).read_bytes()

    # 이름이 서로 맞바뀌는 경우: 00002.jpg가 먼저 찍힘 → 00001.jpg로, 00001.jpg는 00002.jpg로
    first = shot("00002.jpg", 0)
    second = shot("00001.jpg", 5)
    rows = build_rename_plan([tmp_path])
    apply_rename_plan(rows, tmp_path)
    assert (tmp_path / "00001.jpg").read_bytes() == first
    plan = history_undo.build_undo_plan(_entry("시간순 정렬", next(tmp_path.glob("rename_log_*.jsonl"))))
    assert not plan.problems
    history_undo.apply_undo(plan)
    assert (tmp_path / "00002.jpg").read_bytes() == first and (tmp_path / "00001.jpg").read_bytes() == second
    assert not list(tmp_path.glob(".pixthrough-undo-*"))


def test_copy_and_unknown_kinds_are_not_undoable(tmp_path):
    log_file = tmp_path / "apply_log_x.jsonl"
    log_file.write_text("{}\n", encoding="utf-8")
    assert not history_undo.can_undo(_entry("베스트컷 복사", log_file))
    assert not history_undo.can_undo(_entry("C컷 이동", tmp_path / "missing.jsonl"))
    with pytest.raises(ValueError):
        history_undo.build_undo_plan(_entry("라이트룸 별점", log_file))
