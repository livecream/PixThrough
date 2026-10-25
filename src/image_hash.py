"""M2 2차 보강: 사진끼리 얼마나 비슷한지 재는 perceptual hash(pHash).

PROJECT_SPEC.md 4.2절 "perceptual hash(이미지 유사도)로 시간 간격만으로는 부정확한
경우 보완"을 위한 것으로, 장면을 실제로 어떻게 나눌지는 grouping.split_dissimilar가
정한다. 이 모듈은 사진 한 장 → 64비트 지문만 만든다.

imagehash 라이브러리의 phash와 같은 방식(32×32 흑백으로 줄여 DCT, 왼쪽 위 8×8
저주파 성분이 중앙값보다 큰지)을 OpenCV로 직접 구현해, 패키징된 앱에 scipy 같은
무거운 의존성이 따라오지 않게 했다. 지문 두 개의 다른 비트 수(0~64)가 클수록 덜
비슷한 사진이다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import cv2
import numpy as np
from PIL import Image, ImageOps

HASH_BITS = 64
_HASH_SIZE = 8  # 8×8 = 64비트
_DCT_SIZE = 32
# JPEG는 이 크기 근처까지 디코더가 바로 줄여서 읽게 한다(원본 전체 디코딩보다 훨씬 빠름).
_DRAFT_SIZE = (128, 128)


def perceptual_hash(path: Path) -> int | None:
    """사진의 64비트 pHash. 파일을 열 수 없으면 None(비교 대상에서 빠짐)."""
    try:
        with Image.open(path) as img:
            img.draft("L", _DRAFT_SIZE)
            gray = ImageOps.exif_transpose(img).convert("L")
            small = gray.resize((_DCT_SIZE, _DCT_SIZE), Image.Resampling.LANCZOS)
    except Exception:
        return None
    dct = cv2.dct(np.asarray(small, dtype=np.float32))
    low = dct[:_HASH_SIZE, :_HASH_SIZE]
    bits = (low > np.median(low)).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def hash_distance(a: int, b: int) -> int:
    """두 지문의 다른 비트 수. 0이면 사실상 같은 사진, 64면 정반대."""
    return (a ^ b).bit_count()


def hashes_for(
    paths: list[Path],
    cache: dict[Path, tuple[float, int, int | None]] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> dict[Path, int | None]:
    """사진마다 지문을 구한다. cache가 주어지면 (수정 시각, 크기)가 그대로인 파일은
    다시 계산하지 않는다(장면 간격·민감도를 바꿔 다시 묶을 때 즉시 끝나도록)."""
    result: dict[Path, int | None] = {}
    total = len(paths)
    for i, path in enumerate(paths, start=1):
        try:
            stat = path.stat()
            key = (stat.st_mtime, stat.st_size)
        except OSError:
            result[path] = None
            continue
        cached = cache.get(path) if cache is not None else None
        if cached is not None and cached[:2] == key:
            result[path] = cached[2]
        else:
            value = perceptual_hash(path)
            result[path] = value
            if cache is not None:
                cache[path] = (*key, value)
        if on_progress is not None:
            on_progress(i, total)
    return result
