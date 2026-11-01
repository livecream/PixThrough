import pytest
from PIL import Image

from contact_sheet import SheetItem, make_contact_sheet

def _jpeg(path, color=(200, 100, 50), size=(300, 200)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, "JPEG")
    return path

def test_contact_sheet_writes_multi_page_pdf_without_touching_photos(tmp_path):
    photos = [_jpeg(tmp_path / "in" / f"IMG_{i:04d}.jpg", (i * 9 % 255, 80, 120)) for i in range(30)]
    before = {p: p.read_bytes() for p in photos}
    items = [SheetItem(p, scene_id=i // 3 + 1) for i, p in enumerate(photos)]
    progress = []
    out = tmp_path / "out" / "sheet.pdf"
    pages = make_contact_sheet(items, out, "테스트 촬영", on_progress=lambda d, t: progress.append((d, t)))
    assert out.read_bytes().startswith(b"%PDF")
    assert pages == 2  # 한 쪽에 4열 × 6줄 = 24칸
    assert progress[-1] == (30, 30)
    assert all(p.read_bytes() == before[p] for p in photos)

def test_contact_sheet_keeps_broken_photo_as_grey_cell(tmp_path):
    good = _jpeg(tmp_path / "a.jpg")
    broken = tmp_path / "b.jpg"
    broken.write_bytes(b"not a jpeg")
    out = tmp_path / "sheet.pdf"
    assert make_contact_sheet([SheetItem(good, 1), SheetItem(broken, 2)], out, "t") == 1
    assert out.exists()

def test_contact_sheet_rejects_empty(tmp_path):
    with pytest.raises(ValueError):
        make_contact_sheet([], tmp_path / "x.pdf", "t")
