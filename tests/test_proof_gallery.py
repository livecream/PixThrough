import csv
import json
import os
import zipfile

import pytest
from PIL import Image

from proof_gallery import (
    ClientSelection,
    add_watermark,
    build_proof,
    copy_selection,
    match_selection,
    parse_selection,
    render_page,
    ProofPhoto,
)


def _jpeg(path, size=(800, 600), when=0, orientation=None, gps=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    exif = Image.Exif()
    exif[0x010F] = "TestCam"
    if orientation is not None:
        exif[0x0112] = orientation
    if gps:
        exif[0x8825] = {1: "N", 2: (37.0, 30.0, 0.0)}
    Image.new("RGB", size, (120, 140, 160)).save(path, "JPEG", exif=exif.tobytes(), quality=95)
    os.utime(path, (1_700_000_000 + when, 1_700_000_000 + when))
    return path


def _photos(folder):
    _jpeg(folder / "b.jpg", when=10)
    _jpeg(folder / "a.jpg", when=0, orientation=6, gps=True)
    (folder / "a.CR3").write_bytes(b"raw")  # 짝 RAW는 사진 하나로
    return folder


def test_build_proof_makes_small_photos_without_exif_and_a_zip(tmp_path):
    src = _photos(tmp_path / "shoot")
    result = build_proof(src, tmp_path / "out", "김씨 가족", long_edge=300)

    assert result.package_dir == tmp_path / "out" / "김씨 가족"
    assert [p.id for p in result.photos] == ["a.jpg", "b.jpg"]  # 촬영 시각순
    assert result.failed == []
    first = Image.open(result.package_dir / "photos" / "0001.jpg")
    assert first.size == (225, 300)  # 회전 반영 후 긴 변 300
    assert len(first.getexif()) == 0  # GPS 포함 촬영 정보 없음
    manifest = json.loads((result.package_dir / "manifest.json").read_text(encoding="utf-8"))
    assert [p["id"] for p in manifest["photos"]] == ["a.jpg", "b.jpg"]
    page = (result.package_dir / "index.html").read_text(encoding="utf-8")
    assert "김씨 가족" in page and "photos/0002.jpg" in page
    with zipfile.ZipFile(result.zip_path) as z:
        names = z.namelist()
    assert "김씨 가족/index.html" in names and "김씨 가족/photos/0001.jpg" in names
    assert sorted(p.name for p in src.iterdir()) == ["a.CR3", "a.jpg", "b.jpg"]  # 원본 그대로


def test_build_proof_never_overwrites_and_refuses_overlap(tmp_path):
    src = _photos(tmp_path / "shoot")
    first = build_proof(src, tmp_path / "out", "셀렉", long_edge=200)
    second = build_proof(src, tmp_path / "out", "셀렉", long_edge=200)
    assert second.package_dir.name == "셀렉 (1)"
    assert first.package_dir.exists()
    with pytest.raises(ValueError):
        build_proof(src, src / "proof", "셀렉")
    with pytest.raises(ValueError):
        build_proof(src, tmp_path / "out", "셀렉", long_edge=50)


def test_build_proof_skips_broken_photo(tmp_path):
    src = _photos(tmp_path / "shoot")
    (src / "c.jpg").write_bytes(b"not an image")
    result = build_proof(src, tmp_path / "out", "셀렉", long_edge=200)
    assert [p.id for p in result.photos] == ["a.jpg", "b.jpg"]
    assert [path.name for path, _ in result.failed] == ["c.jpg"]


def test_watermark_changes_pixels_and_empty_text_does_nothing():
    image = Image.new("RGB", (400, 300), (30, 30, 30))
    assert add_watermark(image, "  ") is image
    marked = add_watermark(image, "PixThrough 샘플")
    assert marked.size == image.size
    assert marked.tobytes() != image.tobytes()


def test_page_escapes_script_end_in_names():
    page = render_page("<b>제목</b>", [ProofPhoto("x</script>.jpg", None, "photos/0001.jpg")])
    assert "<b>제목</b>" not in page
    assert "x</script>" not in page


def test_parse_selection_reads_page_result_json():
    text = json.dumps({
        "version": 1, "title": "셀렉", "memo": "밝게",
        "selected": [{"id": "sub/a.jpg", "name": "a.jpg", "note": "보정 많이"}, {"id": "b.jpg", "note": ""}],
    })
    selection = parse_selection("﻿" + text)
    assert selection.ids == ["sub/a.jpg", "b.jpg"]
    assert selection.notes == {"sub/a.jpg": "보정 많이"}
    assert selection.memo == "밝게"


def test_parse_selection_reads_pasted_list_with_notes_and_memo():
    selection = parse_selection("a.jpg - 밝게\n\n- b.jpg\nb.jpg\n\n메모: 전체적으로\n따뜻하게")
    assert selection.ids == ["a.jpg", "b.jpg"]
    assert selection.notes == {"a.jpg": "밝게"}
    assert selection.memo == "전체적으로\n따뜻하게"


def test_match_selection_by_path_then_unique_name(tmp_path):
    src = tmp_path / "shoot"
    _jpeg(src / "a.jpg")
    _jpeg(src / "day1" / "same.jpg", when=1)
    _jpeg(src / "day2" / "same.jpg", when=2)
    _jpeg(src / "day2" / "c.JPG", when=3)
    selection = ClientSelection(
        ids=["day1/same.jpg", "same.jpg", "C", "a.jpeg", "zzz.jpg", "a.jpg"], notes={"C": "좋아요"}
    )
    matched, missing = match_selection(selection, src, recursive=True)
    assert [m.id for m in matched] == ["day1/same.jpg", "day2/c.JPG", "a.jpg"]
    assert matched[1].note == "좋아요"
    assert missing == ["same.jpg", "zzz.jpg"]  # 이름이 둘 이상이면 고르지 않음


def test_copy_selection_copies_photo_and_raw_and_writes_list(tmp_path):
    src = _photos(tmp_path / "shoot")
    matched, missing = match_selection(ClientSelection(ids=["a", "b.jpg"], notes={"a": "밝게"}), src)
    assert missing == []
    results, report = copy_selection(matched, tmp_path / "picked", src)
    out = tmp_path / "picked"
    assert (out / "a.jpg").exists() and (out / "b.jpg").exists() and (out / "RAW" / "a.CR3").exists()
    assert (src / "a.jpg").exists() and (src / "a.CR3").exists()  # 복사라 원본 그대로
    assert len(results) == 2
    with open(report, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    assert rows[0] == ["파일", "원본 경로", "메모"]
    assert rows[1][0] == "a.jpg" and rows[1][2] == "밝게"
    with pytest.raises(ValueError):
        copy_selection(matched, src / "picked", src)
