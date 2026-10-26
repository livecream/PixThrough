from PIL import Image

import io

from thumbnails import load_preview_image, make_thumbnail, make_thumbnail_jpeg


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


def test_make_thumbnail_applies_exif_rotation(tmp_path):
    """세로로 찍은 사진(EXIF Orientation 6)이 표 썸네일에서 옆으로 눕지 않아야 한다."""
    path = tmp_path / "portrait.jpg"
    exif = Image.Exif()
    exif[0x0112] = 6
    Image.new("RGB", (200, 100), (10, 20, 30)).save(path, "jpeg", exif=exif)

    thumb = make_thumbnail(path, size=(64, 64))

    assert thumb.size == (32, 64)


def test_make_thumbnail_reads_large_jpeg_at_reduced_size(tmp_path, monkeypatch):
    """큰 JPEG는 원본 전체가 아니라 축소 디코딩(draft)으로 읽는다."""
    path = _make_jpeg(tmp_path / "big.jpg", size=(4000, 3000))
    from PIL import ImageFile

    decoded_sizes = []
    original_load = ImageFile.ImageFile.load

    def spy_load(self):
        result = original_load(self)
        decoded_sizes.append(self.size)
        return result

    monkeypatch.setattr(ImageFile.ImageFile, "load", spy_load)

    thumb = make_thumbnail(path, size=(64, 64))

    assert thumb.size == (64, 48)
    assert max(decoded_sizes[0]) <= 500  # 4000px 전체가 아니라 1/8 근처로 풀었음


def test_make_thumbnail_jpeg_is_small_decodable_jpeg(tmp_path):
    path = _make_jpeg(tmp_path / "a.jpg", size=(400, 200))

    data = make_thumbnail_jpeg(path, (160, 160))

    with Image.open(io.BytesIO(data)) as img:
        assert img.format == "JPEG"
        assert img.size == (160, 80)


def test_make_thumbnail_jpeg_none_for_corrupt_file(tmp_path):
    path = tmp_path / "broken.jpg"
    path.write_bytes(b"not an image")

    assert make_thumbnail_jpeg(path, (160, 160)) is None
