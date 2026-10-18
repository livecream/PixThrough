from PIL import Image

from thumbnails import load_preview_image, make_thumbnail


def _make_jpeg(path, size=(200, 100), color=(200, 50, 50)):
    Image.new("RGB", size, color).save(path, "jpeg")
    return path


def test_make_thumbnail_shrinks_within_bounds(tmp_path):
    path = _make_jpeg(tmp_path / "a.jpg", size=(200, 100))

    thumb = make_thumbnail(path, size=(64, 64))

    assert thumb is not None
    assert thumb.width <= 64
    assert thumb.height <= 64


def test_make_thumbnail_preserves_aspect_ratio(tmp_path):
    path = _make_jpeg(tmp_path / "a.jpg", size=(200, 100))  # 2:1 비율

    thumb = make_thumbnail(path, size=(64, 64))

    assert thumb.width == 64
    assert thumb.height == 32


def test_make_thumbnail_returns_none_for_corrupt_file(tmp_path):
    path = tmp_path / "broken.jpg"
    path.write_bytes(b"not an image")

    assert make_thumbnail(path) is None


def test_load_preview_image_keeps_original_size(tmp_path):
    path = _make_jpeg(tmp_path / "a.jpg", size=(200, 100))

    img = load_preview_image(path)

    assert img is not None
    assert img.size == (200, 100)


def test_load_preview_image_applies_exif_rotation(tmp_path):
    path = tmp_path / "rotated.jpg"
    exif = Image.Exif()
    exif[0x0112] = 6  # Orientation: 시계 방향 90도 회전해서 봐야 하는 세로 사진
    Image.new("RGB", (200, 100), (10, 20, 30)).save(path, "jpeg", exif=exif)

    img = load_preview_image(path)

    assert img.size == (100, 200)


def test_load_preview_image_returns_none_for_corrupt_file(tmp_path):
    path = tmp_path / "broken.jpg"
    path.write_bytes(b"not an image")

    assert load_preview_image(path) is None
