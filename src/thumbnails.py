"""미리보기 테이블에 쓸 작은 썸네일 이미지를 만든다. Qt에 의존하지 않는
순수 함수라 pytest로 바로 테스트한다. 실패해도 예외를 던지지 않고 None을
반환해 호출자(테이블 렌더링)가 placeholder를 쓸 수 있게 한다."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageOps

THUMBNAIL_SIZE = (64, 64)


def make_thumbnail(path: Path, size: tuple[int, int] = THUMBNAIL_SIZE) -> Image.Image | None:
    """path의 이미지를 size 안에 들어가도록 비율 유지한 채 축소한다. 세로로 찍은
    사진이 표에서 옆으로 눕지 않도록 크게 보기 창(load_preview_image)과 똑같이 EXIF
    회전을 반영한다. 파일을 열 수 없으면(손상 등) None을 반환한다."""
    try:
        img = Image.open(path)
        img.load()
    except Exception:
        return None
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail(size)
    return img


def load_preview_image(path: Path) -> Image.Image | None:
    """크게 보기 창에 띄울 원본 크기 이미지를 연다. 블러 여부는 축소하면 가려지므로
    크기는 줄이지 않고(창에서 맞춤/100% 전환), 세로 사진이 눕지 않도록 EXIF 회전만
    반영한다. 파일을 열 수 없으면(손상 등) None을 반환한다."""
    try:
        img = Image.open(path)
        img.load()
    except Exception:
        return None
    return ImageOps.exif_transpose(img).convert("RGB")
