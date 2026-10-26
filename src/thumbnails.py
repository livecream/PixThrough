"""미리보기 테이블에 쓸 작은 썸네일 이미지를 만든다. Qt에 의존하지 않는
순수 함수라 pytest로 바로 테스트한다. 실패해도 예외를 던지지 않고 None을
반환해 호출자(테이블 렌더링)가 placeholder를 쓸 수 있게 한다."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageOps

from raw_preview import open_image

THUMBNAIL_SIZE = (64, 64)


def make_thumbnail(path: Path, size: tuple[int, int] = THUMBNAIL_SIZE) -> Image.Image | None:
    """path의 이미지를 size 안에 들어가도록 비율 유지한 채 축소한다. 세로로 찍은
    사진이 표에서 옆으로 눕지 않도록 크게 보기 창(load_preview_image)과 똑같이 EXIF
    회전을 반영한다. 파일을 열 수 없으면(손상 등) None을 반환한다.

    JPEG는 디코더가 원하는 크기 근처(썸네일의 2배)까지 바로 줄여 읽게 해(draft) 원본
    전체를 풀지 않는다 — 2400만 화소 사진 기준 장당 ~530ms → ~70ms. 다른 형식은 draft가
    아무 일도 하지 않아 예전과 같다."""
    try:
        img = open_image(path, draft=("RGB", (size[0] * 2, size[1] * 2)))
        img.load()
    except Exception:
        return None
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail(size)
    return img


def make_thumbnail_jpeg(path: Path, size: tuple[int, int]) -> bytes | None:
    """make_thumbnail 결과를 JPEG로 압축한 바이트. 갤러리용 큰 썸네일을 수천 장 들고 있어도
    메모리를 적게 쓰도록(160px 기준 장당 수 KB) 압축해 보관한다."""
    img = make_thumbnail(path, size)
    if img is None:
        return None
    buffer = io.BytesIO()
    img.save(buffer, "JPEG", quality=88)
    return buffer.getvalue()


def load_preview_image(path: Path) -> Image.Image | None:
    """크게 보기 창에 띄울 원본 크기 이미지를 연다. 블러 여부는 축소하면 가려지므로
    크기는 줄이지 않고(창에서 맞춤/100% 전환), 세로 사진이 눕지 않도록 EXIF 회전만
    반영한다(RAW는 안에 든 미리보기). 파일을 열 수 없으면(손상 등) None을 반환한다."""
    try:
        img = open_image(path)
        img.load()
    except Exception:
        return None
    return ImageOps.exif_transpose(img).convert("RGB")
