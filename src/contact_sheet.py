"""컨택트 시트: 남기는 컷을 A4 PDF 몇 장에 장면 순서대로 모은다(팀·고객에게 "이렇게 골랐다"를
보여주거나 출력해서 볼 때). 사용자 결정: PDF.

한 칸에 사진(회전 반영, RAW는 미리보기)과 아래에 "장면 n · 파일 이름"을 쓰고, 페이지마다 위에
제목·날짜·장 수, 아래에 쪽 번호를 넣는다. 사진 파일은 읽기만 한다.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw

import history
from parallel import map_in_order
from proof_gallery import korean_font
from thumbnails import make_thumbnail

DPI = 150
PAGE = (1240, 1754)  # A4 세로, 150dpi
MARGIN = 70
COLUMNS = 4
HEADER = 90
FOOTER = 50
CAPTION = 44
GAP = 18


@dataclass(frozen=True)
class SheetItem:
    path: Path
    scene_id: int


def _cell_size(columns: int) -> tuple[int, int]:
    width = (PAGE[0] - 2 * MARGIN - (columns - 1) * GAP) // columns
    return width, int(width * 2 / 3) + CAPTION  # 3:2 사진 + 글자 두 줄


def _fit_text(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> str:
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text + "…"


def make_contact_sheet(
    items: list[SheetItem],
    output: Path,
    title: str,
    columns: int = COLUMNS,
    on_progress: Callable[[int, int], None] | None = None,
) -> int:
    """items(장면 순서)를 output PDF로. 쓴 쪽 수를 돌려준다. 열 수 없는 사진은 회색 칸으로."""
    if not items:
        raise ValueError("남기는 컷이 없습니다.")
    cell_w, cell_h = _cell_size(columns)
    photo_h = cell_h - CAPTION
    thumbs = map_in_order(lambda item: make_thumbnail(item.path, (cell_w * 2, photo_h * 2)), items,
                          on_progress=on_progress)
    rows_per_page = (PAGE[1] - 2 * MARGIN - HEADER - FOOTER + GAP) // (cell_h + GAP)
    per_page = rows_per_page * columns
    pages_total = (len(items) + per_page - 1) // per_page
    title_font, small_font, caption_font = korean_font(30), korean_font(18), korean_font(17)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d")
    pages = []
    for page_number in range(pages_total):
        page = Image.new("RGB", PAGE, "white")
        draw = ImageDraw.Draw(page)
        draw.text((MARGIN, MARGIN), _fit_text(draw, title, title_font, PAGE[0] - 2 * MARGIN - 260),
                  fill="black", font=title_font)
        info = f"{stamp} · {len(items)}장 · 장면 {len({i.scene_id for i in items})}개"
        draw.text((PAGE[0] - MARGIN - draw.textlength(info, font=small_font), MARGIN + 10), info,
                  fill=(90, 90, 90), font=small_font)
        draw.line((MARGIN, MARGIN + HEADER - 30, PAGE[0] - MARGIN, MARGIN + HEADER - 30), fill=(200, 200, 200), width=2)
        chunk = range(page_number * per_page, min(len(items), (page_number + 1) * per_page))
        for slot, index in enumerate(chunk):
            item, thumb = items[index], thumbs[index]
            x = MARGIN + (slot % columns) * (cell_w + GAP)
            y = MARGIN + HEADER + (slot // columns) * (cell_h + GAP)
            if thumb is None:
                draw.rectangle((x, y, x + cell_w, y + photo_h), fill=(225, 225, 225))
            else:
                thumb.thumbnail((cell_w, photo_h), Image.Resampling.LANCZOS)
                page.paste(thumb, (x + (cell_w - thumb.width) // 2, y + (photo_h - thumb.height) // 2))
            draw.text((x, y + photo_h + 6), f"장면 {item.scene_id}", fill=(52, 120, 70), font=caption_font)
            draw.text((x, y + photo_h + 24), _fit_text(draw, item.path.name, caption_font, cell_w),
                      fill=(40, 40, 40), font=caption_font)
        footer = f"{page_number + 1} / {pages_total}"
        draw.text(((PAGE[0] - draw.textlength(footer, font=small_font)) / 2, PAGE[1] - MARGIN), footer,
                  fill=(120, 120, 120), font=small_font)
        pages.append(page)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    pages[0].save(output, "PDF", resolution=DPI, save_all=True, append_images=pages[1:])
    history.record("컨택트 시트", f"{len(items)}장 · {len(pages)}쪽 PDF ({output.name})", output.parent, output,
                   source=history.common_folder(i.path for i in items))
    return len(pages)
