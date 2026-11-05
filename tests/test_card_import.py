import datetime
import os

import pytest
from PIL import Image

import card_import
from card_import import STATUS_COPY, STATUS_EXISTS, STATUS_RENAMED, apply_import, build_import_plan


def _jpeg(path, when="2026:11:03 10:00:00", color=(10, 20, 30)):
    path.parent.mkdir(parents=True, exist_ok=True)
    exif = Image.Exif()
    exif[0x8769] = {0x9003: when}  # ExifIFD DateTimeOriginal
    Image.new("RGB", (40, 30), color).save(path, "JPEG", exif=exif.tobytes())
    return path


def _card(tmp_path):
    card = tmp_path / "card"
    _jpeg(card / "DCIM" / "100CANON" / "IMG_0001.JPG", "2026:11:03 10:00:00")
    (card / "DCIM" / "100CANON" / "IMG_0001.CR3").write_bytes(b"raw-1")  # EXIF 못 읽음 → 수정 시각
    _jpeg(card / "DCIM" / "100CANON" / "IMG_0002.JPG", "2026:11:04 09:00:00")
    (card / "DCIM" / "100CANON" / "notes.txt").write_text("x")
    (card / "DCIM" / "100CANON" / "._IMG_0001.JPG").write_bytes(b"mac")
    (card / ".Trashes").mkdir()
    (card / ".Trashes" / "IMG_9.JPG").write_bytes(b"trash")
    raw = card / "DCIM" / "100CANON" / "IMG_0001.CR3"
    stamp = datetime.datetime(2026, 11, 3, 10, 0, 0).timestamp()
    os.utime(raw, (stamp, stamp))
    return card


def test_plan_groups_by_capture_date_and_skips_hidden_and_other(tmp_path):
    card = _card(tmp_path)
    plan = build_import_plan(card, tmp_path / "photos", tmp_path / "backup")
    assert sorted(r.relative.as_posix() for r in plan.rows) == [
        "2026-11-03/IMG_0001.CR3", "2026-11-03/IMG_0001.JPG", "2026-11-04/IMG_0002.JPG",
    ]
    assert all(r.status == STATUS_COPY and r.backup_status == STATUS_COPY for r in plan.rows)
    assert plan.skipped_other == 1
    assert plan.total_bytes == 2 * sum(r.size for r in plan.rows)


def test_import_copies_verified_to_both_and_second_run_skips(tmp_path):
    card = _card(tmp_path)
    before = {p: p.read_bytes() for p in card.rglob("*") if p.is_file()}
    dest, backup = tmp_path / "photos", tmp_path / "backup"
    outcome = apply_import(build_import_plan(card, dest, backup))
    assert outcome.safe_to_erase
    for root in (dest, backup):
        assert (root / "2026-11-03" / "IMG_0001.CR3").read_bytes() == b"raw-1"
        assert (root / "2026-11-04" / "IMG_0002.JPG").exists()
    assert not list(dest.rglob("*.part"))
    assert {p: p.read_bytes() for p in card.rglob("*") if p.is_file()} == before  # 카드 그대로
    assert outcome.log_file.exists()
    # 다시 가져오면 전부 "이미 가져옴"
    again = build_import_plan(card, dest, backup)
    assert all(r.status == STATUS_EXISTS and r.backup_status == STATUS_EXISTS for r in again.rows)
    assert again.to_copy == [] and again.total_bytes == 0
    second = apply_import(again)
    assert second.safe_to_erase and not any(r.copied or r.backed_up for r in second.results)


def test_same_name_different_photo_keeps_pair_names(tmp_path):
    card = _card(tmp_path)
    dest = tmp_path / "photos"
    apply_import(build_import_plan(card, dest))
    # 카드 번호가 한 바퀴 돌아 같은 이름의 다른 사진
    _jpeg(card / "DCIM" / "101CANON" / "IMG_0001.JPG", "2026:11:03 15:00:00", color=(200, 0, 0))
    plan = build_import_plan(card, dest)
    renamed = [r for r in plan.rows if r.status == STATUS_RENAMED]
    assert [r.relative.as_posix() for r in renamed] == ["2026-11-03/IMG_0001_101CANON.JPG"]
    outcome = apply_import(plan)
    assert outcome.safe_to_erase
    assert (dest / "2026-11-03" / "IMG_0001.JPG").read_bytes() != (dest / "2026-11-03" / "IMG_0001_101CANON.JPG").read_bytes()


def test_import_never_overwrites_and_reports_error(tmp_path, monkeypatch):
    card = _card(tmp_path)
    dest = tmp_path / "photos"
    plan = build_import_plan(card, dest)
    # 계획 뒤에 같은 이름의 다른 파일이 생김
    target = dest / "2026-11-04" / "IMG_0002.JPG"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"someone else")
    outcome = apply_import(plan)
    assert target.read_bytes() == b"someone else"
    assert not outcome.safe_to_erase and len(outcome.errors) == 1


def test_corrupt_copy_is_removed_and_reported(tmp_path, monkeypatch):
    card = _card(tmp_path)
    dest = tmp_path / "photos"
    real_hash = card_import._hash
    monkeypatch.setattr(card_import, "_hash", lambda p: "bad" if p.name.endswith(".part") else real_hash(p))
    outcome = apply_import(build_import_plan(card, dest))
    assert len(outcome.errors) == 3 and not outcome.safe_to_erase
    assert not [p for p in dest.rglob("*") if p.is_file() and not p.name.startswith("import_log_")]


def test_rejects_overlapping_folders(tmp_path):
    card = _card(tmp_path)
    with pytest.raises(ValueError):
        build_import_plan(card, card / "out")
    with pytest.raises(ValueError):
        build_import_plan(card, tmp_path / "a", tmp_path / "a" / "b")
