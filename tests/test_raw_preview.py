"""RAW만 찍은 폴더 지원: rawpy를 가짜 모듈로 바꿔, RAW 안의 미리보기 JPEG 하나로
스캔·점수·썸네일·지문·파일 복사가 모두 돌아가는지 확인한다(실제 RAW 파일 없이)."""

import datetime
import enum
import io
import sys
import types

import numpy as np
import pytest
from PIL import Image

import raw_preview
from file_ops import apply_selection
from image_hash import perceptual_hash
from report import ReportRow
from scanner import extract_metadata, scan_and_extract, scan_folder
from scoring import score_photo
from thumbnails import load_preview_image, make_thumbnail

PREVIEW_SIZE = (120, 80)  # 가로로 긴 미리보기
SHOT_AT = datetime.datetime(2026, 5, 1, 14, 30, 5)


class _ThumbFormat(enum.Enum):
    JPEG = 1
    BITMAP = 2


def _preview_jpeg() -> bytes:
    rng = np.random.default_rng(0)
    arr = rng.integers(0, 256, (PREVIEW_SIZE[1], PREVIEW_SIZE[0], 3), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, "jpeg")
    return buf.getvalue()


@pytest.fixture
def fake_rawpy(monkeypatch):
    """파일 내용이 b"no-thumb"면 미리보기가 없는 RAW, 아니면 PREVIEW_SIZE JPEG를 돌려준다."""
    state = types.SimpleNamespace(flip=0, timestamp=SHOT_AT)
    jpeg = _preview_jpeg()

    class _Raw:
        def __init__(self, path):
            self._path = path
            self.sizes = types.SimpleNamespace(flip=state.flip)
            self.other = types.SimpleNamespace(timestamp=state.timestamp)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_thumb(self):
            with open(self._path, "rb") as f:
                if f.read() == b"no-thumb":
                    raise RuntimeError("LibRawNoThumbnailError")
            return types.SimpleNamespace(format=_ThumbFormat.JPEG, data=jpeg)

    module = types.SimpleNamespace(imread=_Raw, ThumbFormat=_ThumbFormat)
    monkeypatch.setitem(sys.modules, "rawpy", module)
    return state


def _raw(path):
    path.write_bytes(b"fake raw")
    return path


def _jpg(path):
    Image.new("RGB", (16, 16), (128, 128, 128)).save(path, "jpeg")
    return path


def test_scan_skips_raw_by_default(tmp_path):
    _raw(tmp_path / "A.CR3")

    assert scan_folder(tmp_path) == []


def test_scan_includes_only_raw_without_photo_sibling(tmp_path):
    _jpg(tmp_path / "IMG_1.JPG")
    _raw(tmp_path / "IMG_1.CR3")  # 짝 JPG 있음 → JPG만
    lone = _raw(tmp_path / "IMG_2.CR3")

    assert scan_folder(tmp_path, include_unpaired_raw=True) == [tmp_path / "IMG_1.JPG", lone]


def test_scan_treats_raw_subfolder_as_paired_with_parent_photo(tmp_path):
    _jpg(tmp_path / "IMG_1.jpg")
    (tmp_path / "RAW").mkdir()
    _raw(tmp_path / "RAW" / "IMG_1.CR3")

    assert scan_folder(tmp_path, recursive=True, include_unpaired_raw=True) == [tmp_path / "IMG_1.jpg"]


def test_metadata_reads_preview_size_and_capture_time(fake_rawpy, tmp_path):
    meta = extract_metadata(_raw(tmp_path / "A.CR3"))

    assert meta.error is None
    assert (meta.width, meta.height) == PREVIEW_SIZE
    assert meta.datetime_original == SHOT_AT
    assert meta.exif_source == "exif"


def test_portrait_raw_is_rotated_upright(fake_rawpy, tmp_path):
    fake_rawpy.flip = 6  # 세로로 찍음(시계 90°)
    path = _raw(tmp_path / "A.NEF")

    assert (extract_metadata(path).width, extract_metadata(path).height) == PREVIEW_SIZE[::-1]
    assert raw_preview.open_image(path).size == PREVIEW_SIZE[::-1]
    assert load_preview_image(path).size == PREVIEW_SIZE[::-1]


def test_raw_without_preview_is_reported_not_crashed(fake_rawpy, tmp_path):
    path = tmp_path / "A.CR3"
    path.write_bytes(b"no-thumb")

    meta = extract_metadata(path)

    assert meta.error is not None
    assert score_photo(meta).error is not None
    assert make_thumbnail(path) is None
    assert perceptual_hash(path) is None


def test_raw_only_folder_scores_thumbnails_and_hashes(fake_rawpy, tmp_path):
    _raw(tmp_path / "A.CR3")

    [meta] = scan_and_extract(tmp_path, include_unpaired_raw=True)
    score = score_photo(meta)

    assert score.error is None
    assert score.sharpness > 0
    assert make_thumbnail(meta.path) is not None
    assert perceptual_hash(meta.path) is not None


def test_selected_raw_only_photo_goes_to_raw_subfolder(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    raw = _raw(src / "A.CR3")
    out = tmp_path / "out"
    row = ReportRow(
        path=raw, scene_id=1, sharpness=0.0, exposure=1.0, face=None, total=1.0,
        selected=True, reason="선택됨: 장면 내 최고 종합점수",
    )

    [result] = apply_selection([row], out)

    assert result.destination == out / "RAW" / "A.CR3"
    assert result.raw_source is None
    assert (out / "RAW" / "A.CR3").read_bytes() == b"fake raw"
    assert raw.exists()  # 기본은 복사, 원본 그대로
    assert not (out / "A.CR3").exists()
