"""회귀 기준선(test_photos/baseline.json)을 바꾸기 전에, 무엇이 왜 바뀌었는지 사람이 보고
판단할 수 있게 하는 도우미(update_regression_baseline.py가 쓴다).

- diff_selection: 예전 기준선과 지금 결과의 차이(새로 뽑힘/빠짐/새 파일/없어진 파일)
- write_review_html: 바뀐 장면만(기준선이 없으면 모든 장면) 사진을 나란히 놓은 검토 페이지.
  지금 뽑힌 컷은 초록 테두리, 예전에 뽑혔던 컷은 주황 점선, 아래에 종합점수·장면 안
  선명도 순위·눈뜸. 썸네일은 HTML 안에 넣어 파일 하나로 열린다. test_photos/처럼
  개인 사진 폴더 안에 쓰므로 저장소에는 올라가지 않는다.
"""

from __future__ import annotations

import base64
import html
from dataclasses import dataclass, field
from pathlib import Path

from report import ReportRow
from thumbnails import make_thumbnail_jpeg

THUMB = 220


@dataclass
class SelectionDiff:
    added: list[str] = field(default_factory=list)  # 지금 새로 뽑힘
    removed: list[str] = field(default_factory=list)  # 예전엔 뽑혔는데 지금 빠짐
    new_files: list[str] = field(default_factory=list)  # 기준선에 없던 사진
    missing_files: list[str] = field(default_factory=list)  # 기준선엔 있는데 지금 없는 사진

    @property
    def changed(self) -> bool:
        return bool(self.added or self.removed or self.new_files or self.missing_files)


def diff_selection(old: dict[str, bool], new: dict[str, bool]) -> SelectionDiff:
    diff = SelectionDiff()
    for name in sorted(new):
        if name not in old:
            diff.new_files.append(name)
        elif new[name] and not old[name]:
            diff.added.append(name)
        elif old[name] and not new[name]:
            diff.removed.append(name)
    diff.missing_files = sorted(name for name in old if name not in new)
    return diff


def format_diff(diff: SelectionDiff) -> str:
    if not diff.changed:
        return "기준선과 같습니다."
    lines = []
    for title, names in (
        ("새로 뽑힘", diff.added),
        ("빠짐(예전엔 뽑힘)", diff.removed),
        ("기준선에 없던 사진", diff.new_files),
        ("지금은 없는 사진", diff.missing_files),
    ):
        if names:
            lines.append(f"{title} {len(names)}장: " + ", ".join(names))
    return "\n".join(lines)


def _scene_ranks(rows: list[ReportRow]) -> dict[Path, tuple[int, int]]:
    scenes: dict[int, list[ReportRow]] = {}
    for row in rows:
        scenes.setdefault(row.scene_id, []).append(row)
    ranks = {}
    for members in scenes.values():
        ordered = sorted(members, key=lambda r: r.sharpness, reverse=True)
        for rank, row in enumerate(ordered, start=1):
            ranks[row.path] = (rank, len(members))
    return ranks


def _thumbnail_src(path: Path) -> str:
    data = make_thumbnail_jpeg(path, (THUMB, THUMB))
    if data is None:
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")


def write_review_html(
    rows: list[ReportRow], old: dict[str, bool] | None, root: Path, out_path: Path
) -> int:
    """검토 페이지를 쓰고, 담은 장면 수를 돌려준다. old가 None(기준선 없음)이면 모든 장면."""
    def key(row: ReportRow) -> str:
        return str(Path(row.path).relative_to(root))

    scenes: dict[int, list[ReportRow]] = {}
    for row in rows:
        scenes.setdefault(row.scene_id, []).append(row)
    ranks = _scene_ranks(rows)
    sections = []
    for scene_id, members in scenes.items():
        changed = old is None or any(old.get(key(r)) != r.selected for r in members)
        if not changed:
            continue
        before = [key(r) for r in members if old and old.get(key(r))]
        after = [key(r) for r in members if r.selected]
        heading = f"장면 {scene_id} · {len(members)}장"
        if old is not None:
            heading += f" — 예전: {', '.join(before) or '없음'} → 지금: {', '.join(after) or '없음'}"
        cards = []
        for row in members:
            classes = ["card"]
            if row.selected:
                classes.append("now")
            if old is not None and old.get(key(row)):
                classes.append("before")
            rank, count = ranks[row.path]
            eyes = "—" if row.face is None else f"{row.face:.2f}"
            src = _thumbnail_src(Path(row.path))
            image = f'<img src="{src}" alt="">' if src else '<div class="noimg">열 수 없음</div>'
            cards.append(
                f'<figure class="{" ".join(classes)}">{image}<figcaption>'
                f"<b>{html.escape(key(row))}</b><br>종합 {row.total:.2f} · 선명도 {rank}위/{count} · "
                f"눈뜸 {eyes}<br><small>{html.escape(row.reason)}</small></figcaption></figure>"
            )
        sections.append(f"<section><h2>{html.escape(heading)}</h2><div class=row>{''.join(cards)}</div></section>")

    legend = (
        "초록 테두리 = 지금 뽑힌 컷, 주황 점선 = 예전 기준선에서 뽑혔던 컷."
        if old is not None
        else "기준선이 없어 모든 장면을 보여줍니다. 초록 테두리 = 지금 뽑힌 컷."
    )
    out_path.write_text(
        "<!doctype html><meta charset=utf-8><title>기준선 검토</title><style>"
        "body{font-family:-apple-system,'Malgun Gothic',sans-serif;margin:16px;background:#fafafa}"
        "h2{font-size:15px;margin:18px 0 6px}.row{display:flex;flex-wrap:wrap;gap:10px}"
        f".card{{margin:0;width:{THUMB}px;padding:4px;border:3px solid transparent;background:#fff}}"
        ".card.now{border-color:#2e9e4f}.card.before{outline:3px dashed #e8890c;outline-offset:2px}"
        f".card img{{max-width:{THUMB}px;max-height:{THUMB}px;display:block;margin:auto}}"
        "figcaption{font-size:12px;margin-top:4px}small{color:#666}.noimg{height:120px;color:#999}"
        f"</style><h1>회귀 기준선 검토</h1><p>{legend} 장면 {len(sections)}개.</p>" + "".join(sections),
        encoding="utf-8",
    )
    return len(sections)
