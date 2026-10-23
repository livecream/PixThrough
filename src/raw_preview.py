"""RAW만 찍은 폴더 지원: RAW 파일 안에 카메라가 넣어 둔 미리보기 JPEG를 꺼내 쓴다.

RAW 현상(디모자이킹)은 사진 1장에 수 초가 걸리고 결과도 카메라 JPEG와 달라 점수 범위가
어긋나므로, 카메라가 RAW 안에 같이 저장한 원본 크기 미리보기 JPEG(보통 카메라 JPEG와
같은 처리 결과)를 그대로 쓴다. 선명도·노출·얼굴 점수, 썸네일, 크게 보기, 유사도 지문이
모두 이 이미지로 계산된다.

같은 이름의 JPG가 함께 있는 RAW는 여기 오지 않는다 — 그 경우는 JPG로 점수를 매기고
RAW는 복사할 때 짝으로 따라간다(file_ops.find_matching_raw).
"""

from __future__ import annotations

import datetime
import io
from pathlib import Path

from PIL import Image, ImageOps

# 스캐너와 같은 목록 (순환 import를 피하려고 scanner가 이 값을 가져다 쓴다)
RAW_EXTENSIONS = {".cr2", ".cr3", ".nef", ".arw", ".dng", ".raf", ".orf", ".rw2"}

# LibRaw의 sizes.flip 값 → 똑바로 세우는 회전
_FLIP_TRANSPOSE = {
    3: Image.Transpose.ROTATE_180,
    5: Image.Transpose.ROTATE_90,  # 반시계 90°
    6: Image.Transpose.ROTATE_270,  # 시계 90°
}


def is_raw(path: Path) -> bool:
    return Path(path).suffix.lower() in RAW_EXTENSIONS


def _read_preview(path: Path) -> tuple[Image.Image, int]:
    """RAW 안의 미리보기 이미지(아직 회전 안 함)와 LibRaw 방향값을 꺼낸다.
    미리보기가 없거나 읽을 수 없는 RAW면 예외."""
    import rawpy  # RAW가 없는 폴더에서는 불러올 필요가 없다

    with rawpy.imread(str(path)) as raw:
        thumb = raw.extract_thumb()
        flip = raw.sizes.flip
    if thumb.format == rawpy.ThumbFormat.JPEG:
        img = Image.open(io.BytesIO(thumb.data))
    else:  # BITMAP: 이미 펼쳐진 RGB 배열
        img = Image.fromarray(thumb.data)
    return img, flip


def open_raw_preview(path: Path, draft: tuple[str, tuple[int, int]] | None = None) -> Image.Image:
    """RAW의 미리보기를 똑바로 세운 이미지로 연다(예외는 호출자가 처리).

    draft=(모드, 크기)를 주면 JPEG 미리보기를 그 크기 근처로 줄여서 디코딩한다(지문처럼
    작은 이미지만 필요할 때 빠르게). 반환 이미지에는 EXIF 방향 정보를 남기지 않아,
    호출자가 일반 사진처럼 exif_transpose를 해도 두 번 돌지 않는다.
    """
    img, flip = _read_preview(path)
    if draft is not None:
        img.draft(*draft)
    img.load()
    if flip in _FLIP_TRANSPOSE:
        img = img.transpose(_FLIP_TRANSPOSE[flip])
    else:
        # LibRaw가 방향을 모르면 미리보기 JPEG 자체의 EXIF 방향을 따른다
        img = ImageOps.exif_transpose(img)
    img.info.pop("exif", None)
    return img


def open_image(path: Path, draft: tuple[str, tuple[int, int]] | None = None) -> Image.Image:
    """사진을 연다: RAW면 똑바로 세운 미리보기, 아니면 Image.open 그대로(회전은 호출자 몫)."""
    if is_raw(path):
        return open_raw_preview(path, draft)
    img = Image.open(path)
    if draft is not None:
        img.draft(*draft)
    return img


def raw_capture_time(path: Path) -> datetime.datetime | None:
    """LibRaw가 읽은 촬영 시각. exifread가 못 읽는 형식(CR3 등)의 대비책."""
    try:
        import rawpy

        with rawpy.imread(str(path)) as raw:
            value = raw.other.timestamp
    except Exception:
        return None
    if isinstance(value, datetime.datetime) and value.year > 1970:
        return value.replace(tzinfo=None)
    return None
