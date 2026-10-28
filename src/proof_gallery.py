"""고객 셀렉: 사진 폴더로 고객이 고를 수 있는 페이지(폴더+zip)를 만들고, 고객이 보낸 선택 결과로
원본을 따로 복사한다.

사용자 결정(PROJECT_SPEC 8절):
- 인터넷 서버 없이: 축소 사진 + index.html(페이지 하나, 스크립트·스타일 내장)을 폴더와 zip으로
  만든다. 올리는 곳(구글 드라이브 등)은 사용자가 정한다. 고객은 브라우저로 열어 사진을 고르고
  "선택 결과 저장"(JSON 파일)이나 "목록 복사"(파일 이름 줄 목록)로 돌려보낸다.
- 대상은 고른 폴더의 사진 전부(촬영 시각순). JPG+RAW 짝은 사진 하나로, RAW만 있는 사진은
  안에 든 미리보기로 보여준다.
- 페이지 사진은 긴 변을 정한 픽셀로 줄이고 회전을 반영해 저장하며, 촬영 정보(EXIF, GPS 위치
  포함)는 넣지 않는다(고객에게 위치가 새지 않게). 워터마크 글자는 넣을 때만.
- 가져오기: 결과 파일이나 붙여넣은 목록을 원본 폴더 사진과 맞춰(경로가 같으면 그대로, 아니면
  파일 이름으로) 보여주고, 확인하면 원본(+같은 이름 RAW)을 고른 폴더로 복사한다(file_ops).
원본 폴더에는 아무것도 쓰지 않는다. 페이지 폴더·결과 폴더가 원본 폴더와 겹치면 거부한다.
"""

from __future__ import annotations

import csv
import datetime
import html
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageFont, ImageOps

import app_log
from file_ops import FileOpResult, apply_selection
from raw_preview import open_image
from report import ReportRow
from scanner import scan_and_extract

log = app_log.get("proof")

RESULT_VERSION = 1
LONG_EDGE_START = 1600
QUALITY = 85


@dataclass(frozen=True)
class ProofPhoto:
    id: str  # 원본 폴더 기준 상대 경로(슬래시) — 고객 결과를 원본과 맞추는 열쇠
    source: Path
    file: str  # 페이지 폴더 안 사진 파일(photos/0001.jpg)


@dataclass(frozen=True)
class ProofResult:
    package_dir: Path
    zip_path: Path
    photos: list[ProofPhoto]
    failed: list[tuple[Path, str]]


@dataclass
class ClientSelection:
    ids: list[str] = field(default_factory=list)  # 고객이 고른 사진(상대 경로 또는 파일 이름)
    notes: dict[str, str] = field(default_factory=dict)  # 사진별 메모
    memo: str = ""  # 전체 메모
    title: str = ""


@dataclass(frozen=True)
class MatchedPick:
    id: str
    source: Path
    note: str


def _guard_overlap(source_dir: Path, target_dir: Path, what: str) -> None:
    source, target = source_dir.resolve(), target_dir.resolve()
    if target == source or source in target.parents or target in source.parents:
        raise ValueError(
            f"{what}가 사진 폴더와 같거나 서로 포함 관계입니다. 원본 폴더와 겹치지 않는 곳을 고르세요."
        )


def list_photos(folder: Path, recursive: bool = False) -> list[tuple[str, Path]]:
    """폴더 사진을 촬영 시각순으로 (상대 경로 id, 경로). JPG+RAW 짝은 사진 쪽 하나."""
    folder = Path(folder)
    metas = scan_and_extract(folder, recursive=recursive, include_unpaired_raw=True)
    return [(m.path.relative_to(folder).as_posix(), m.path) for m in metas]


# --- 워터마크 ------------------------------------------------------------------------
_FONT_CANDIDATES = (
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",  # macOS
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
    "C:/Windows/Fonts/malgun.ttf",  # Windows 맑은 고딕
    "C:/Windows/Fonts/malgunbd.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
)


def _watermark_font(size: int) -> ImageFont.ImageFont:
    """한글이 나오는 시스템 글꼴(없으면 기본 글꼴 — 영문만 제대로 나옴)."""
    for candidate in _FONT_CANDIDATES:
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
    return ImageFont.load_default(size=size)


def add_watermark(image: Image.Image, text: str) -> Image.Image:
    """사진 가운데에 반투명 글자를 넣는다(어두운 테두리로 밝은 사진에서도 보이게)."""
    text = text.strip()
    if not text:
        return image
    base = image.convert("RGBA")
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    size = max(12, base.width // 14)
    font = _watermark_font(size)
    # 사진 너비의 70%를 넘지 않게 줄인다
    while size > 12:
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font, stroke_width=max(1, size // 20))
        if right - left <= base.width * 0.7:
            break
        size = int(size * 0.85)
        font = _watermark_font(size)
    stroke = max(1, size // 20)
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
    x = (base.width - (right - left)) / 2 - left
    y = (base.height - (bottom - top)) / 2 - top
    draw.text((x, y), text, font=font, fill=(255, 255, 255, 110), stroke_width=stroke, stroke_fill=(0, 0, 0, 70))
    return Image.alpha_composite(base, layer).convert("RGB")


def make_proof_image(source: Path, destination: Path, long_edge: int, watermark: str = "") -> None:
    """회전을 반영해 긴 변 long_edge로 줄이고(키우지 않음) EXIF 없이 JPG로 저장한다."""
    with open_image(source, draft=("RGB", (long_edge * 2, long_edge * 2))) as img:
        img.load()
        image = ImageOps.exif_transpose(img).convert("RGB")
    image.thumbnail((long_edge, long_edge), Image.Resampling.LANCZOS)
    image = add_watermark(image, watermark)
    image.save(destination, "JPEG", quality=QUALITY, optimize=True)


# --- 페이지 만들기 ---------------------------------------------------------------------
def _safe_name(title: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]+', " ", title).strip().strip(".")
    return name[:60] or "셀렉"


def _unique_dir(path: Path) -> Path:
    candidate, n = path, 1
    while candidate.exists() or candidate.with_suffix(".zip").exists():
        candidate = path.with_name(f"{path.name} ({n})")
        n += 1
    return candidate


def build_proof(
    folder: Path,
    output_dir: Path,
    title: str,
    long_edge: int = LONG_EDGE_START,
    watermark: str = "",
    max_picks: int = 0,
    recursive: bool = False,
    on_progress: Callable[[int, int], None] | None = None,
) -> ProofResult:
    """output_dir/<제목>/ 에 index.html·photos/·manifest.json을 만들고 같은 이름의 zip도 만든다.
    같은 이름이 있으면 덮어쓰지 않고 " (n)"을 붙인다. 열 수 없는 사진은 건너뛰고 알린다."""
    folder, output_dir = Path(folder), Path(output_dir)
    _guard_overlap(folder, output_dir, "페이지를 만들 폴더")
    if not 200 <= long_edge <= 8000:
        raise ValueError("사진 긴 변은 200~8000px 사이여야 합니다.")
    items = list_photos(folder, recursive)
    if not items:
        raise ValueError("사진 폴더에 사진이 없습니다.")
    package = _unique_dir(output_dir / _safe_name(title or folder.name))
    photos_dir = package / "photos"
    photos_dir.mkdir(parents=True)
    photos: list[ProofPhoto] = []
    failed: list[tuple[Path, str]] = []
    for index, (photo_id, source) in enumerate(items, start=1):
        file = f"photos/{index:04d}.jpg"
        try:
            make_proof_image(source, package / file, long_edge, watermark)
        except Exception as e:  # noqa: BLE001 - 한 장 실패로 전체를 멈추지 않는다
            failed.append((source, str(e)))
        else:
            photos.append(ProofPhoto(photo_id, source, file))
        if on_progress is not None:
            on_progress(index, len(items))
    manifest = {
        "version": RESULT_VERSION,
        "title": title,
        "source_folder": str(folder),
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
        "max_picks": max_picks,
        "photos": [{"id": p.id, "file": p.file} for p in photos],
    }
    (package / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    (package / "index.html").write_text(render_page(title or folder.name, photos, max_picks), encoding="utf-8")
    zip_path = Path(shutil.make_archive(str(package), "zip", root_dir=package.parent, base_dir=package.name))
    log.info("고객 셀렉 페이지: %d장 (실패 %d) → %s", len(photos), len(failed), package)
    return ProofResult(package, zip_path, photos, failed)


def render_page(title: str, photos: list[ProofPhoto], max_picks: int = 0) -> str:
    data = [{"id": p.id, "file": p.file, "name": Path(p.id).name} for p in photos]
    payload = json.dumps({"title": title, "limit": max_picks, "photos": data}, ensure_ascii=False)
    payload = payload.replace("</", "<\\/")  # 스크립트 안에서 태그가 닫히지 않게
    return _PAGE.replace("__TITLE__", html.escape(title)).replace("__DATA__", payload)


# --- 고객 결과 가져오기 -------------------------------------------------------------------
def parse_selection(text: str) -> ClientSelection:
    """페이지가 저장한 결과 JSON, 또는 줄마다 파일 이름 하나인 목록(메모는 "이름 - 메모")."""
    text = text.strip().lstrip("\ufeff")
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, dict) and isinstance(data.get("selected"), list):
        selection = ClientSelection(title=str(data.get("title", "")), memo=str(data.get("memo", "")))
        for item in data["selected"]:
            if isinstance(item, dict) and item.get("id"):
                selection.ids.append(str(item["id"]))
                if item.get("note"):
                    selection.notes[str(item["id"])] = str(item["note"])
            elif isinstance(item, str):
                selection.ids.append(item)
        return selection
    selection = ClientSelection()
    lines = text.splitlines()
    for number, line in enumerate(lines):
        if line.strip().startswith("메모:"):  # 페이지의 "목록 복사"가 끝에 붙이는 전체 메모(여러 줄)
            rest = [line.strip()[len("메모:"):]] + lines[number + 1:]
            selection.memo = "\n".join(rest).strip()
            break
        line = line.strip().lstrip("-•*").strip()
        if not line or line.startswith("#"):
            continue
        name, sep, note = line.partition(" - ")
        name = name.strip()
        if name and name not in selection.ids:
            selection.ids.append(name)
            if sep and note.strip():
                selection.notes[name] = note.strip()
    return selection


def match_selection(
    selection: ClientSelection, folder: Path, recursive: bool = False
) -> tuple[list[MatchedPick], list[str]]:
    """고객이 고른 것을 원본 폴더 사진과 맞춘다. 경로(id)가 같으면 그대로, 아니면 확장자를 뺀
    파일 이름이 하나뿐일 때만 그 사진(둘 이상이면 못 찾음으로 — 잘못 복사하지 않게)."""
    items = list_photos(folder, recursive)
    by_id = {photo_id: path for photo_id, path in items}
    by_stem: dict[str, list[tuple[str, Path]]] = {}
    for photo_id, path in items:
        by_stem.setdefault(path.stem.lower(), []).append((photo_id, path))
    matched: list[MatchedPick] = []
    missing: list[str] = []
    seen: set[str] = set()
    for wanted in selection.ids:
        key = wanted.replace("\\", "/")
        if key in by_id:
            hit = (key, by_id[key])
        else:
            candidates = by_stem.get(Path(key).stem.lower(), [])
            hit = candidates[0] if len(candidates) == 1 else None
        if hit is None:
            missing.append(wanted)
        elif hit[0] not in seen:
            seen.add(hit[0])
            matched.append(MatchedPick(hit[0], hit[1], selection.notes.get(wanted, "")))
    return matched, missing


def copy_selection(
    matched: list[MatchedPick], output_dir: Path, folder: Path,
    on_progress: Callable[[int, int], None] | None = None,
) -> tuple[list[FileOpResult], Path]:
    """고른 사진 원본(+같은 이름 RAW는 RAW/)을 output_dir로 복사하고 목록 CSV를 남긴다."""
    output_dir = Path(output_dir)
    _guard_overlap(Path(folder), output_dir, "복사할 폴더")
    rows = [
        ReportRow(path=pick.source, scene_id=0, sharpness=0.0, exposure=0.0, face=None, total=0.0,
                  selected=True, reason="고객 선택" + (f": {pick.note}" if pick.note else ""))
        for pick in matched
    ]
    report = output_dir / f"client_selection_{datetime.datetime.now():%Y%m%d_%H%M%S}.csv"
    try:
        results = apply_selection(rows, output_dir, on_progress=on_progress)
    finally:  # 일부가 실패해도(ApplyError) 고객이 고른 목록과 메모는 남긴다
        if output_dir.is_dir():
            with open(report, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["파일", "원본 경로", "메모"])
                for pick in matched:
                    writer.writerow([pick.source.name, str(pick.source), pick.note])
    log.info("고객 선택 복사: %d장 → %s", len(results), output_dir)
    return results, report


_PAGE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root { --bg:#f6f6f4; --fg:#1d1d1f; --muted:#6e6e73; --card:#fff; --accent:#2e9e4f; --line:#e2e2e2; }
@media (prefers-color-scheme: dark) { :root { --bg:#161617; --fg:#f2f2f2; --muted:#a1a1a6; --card:#222224; --line:#333; } }
* { box-sizing:border-box; }
body { margin:0; font-family:-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Malgun Gothic",sans-serif; background:var(--bg); color:var(--fg); }
header { position:sticky; top:0; z-index:5; background:var(--bg); border-bottom:1px solid var(--line); padding:12px 16px; }
h1 { font-size:18px; margin:0 0 6px; }
.bar { display:flex; flex-wrap:wrap; gap:8px; align-items:center; }
.count { font-weight:600; margin-right:auto; }
.over { color:#d0382b; }
button { font:inherit; border:1px solid var(--line); background:var(--card); color:var(--fg); border-radius:8px; padding:7px 12px; cursor:pointer; }
button.primary { background:var(--accent); border-color:var(--accent); color:#fff; font-weight:600; }
label.toggle { display:flex; gap:6px; align-items:center; color:var(--muted); }
.help { color:var(--muted); font-size:13px; margin:6px 0 0; }
main { display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); gap:10px; padding:16px; }
.card { position:relative; background:var(--card); border-radius:10px; overflow:hidden; border:3px solid transparent; cursor:pointer; }
.card.picked { border-color:var(--accent); }
.card img { width:100%; aspect-ratio:3/2; object-fit:contain; background:#0001; display:block; }
.card .name { font-size:12px; color:var(--muted); padding:5px 8px; display:flex; justify-content:space-between; }
.card .mark { position:absolute; top:8px; right:8px; width:30px; height:30px; border-radius:50%; background:#0006; color:#fff; display:grid; place-items:center; font-size:18px; }
.card.picked .mark { background:var(--accent); }
.hidden { display:none !important; }
#viewer { position:fixed; inset:0; background:#000; z-index:10; display:flex; flex-direction:column; }
#viewer img { flex:1; min-height:0; object-fit:contain; width:100%; }
#viewer .vbar { display:flex; gap:8px; align-items:center; padding:10px 16px; color:#fff; flex-wrap:wrap; }
#viewer .vbar span { margin-right:auto; }
#viewer input { flex:1; min-width:160px; font:inherit; padding:6px 8px; border-radius:6px; border:0; }
footer { padding:16px; }
textarea { width:100%; min-height:70px; font:inherit; border-radius:8px; border:1px solid var(--line); background:var(--card); color:var(--fg); padding:8px; }
#list { min-height:120px; }
</style>
</head>
<body>
<header>
  <h1>__TITLE__</h1>
  <div class="bar">
    <span class="count" id="count"></span>
    <label class="toggle"><input type="checkbox" id="onlyPicked"> 고른 사진만 보기</label>
    <button id="copy">목록 복사</button>
    <button class="primary" id="save">선택 결과 저장</button>
  </div>
  <p class="help">사진을 눌러 고르세요(다시 누르면 취소). 크게 보려면 사진 아래 이름을 누르세요 — ←/→ 넘기기, Space 고르기, Esc 닫기.
  고른 것은 이 브라우저에 저장되니 창을 닫았다 열어도 남아 있습니다. 다 고르면 <b>선택 결과 저장</b>으로 받은 파일(pixthrough-selection.json)을 보내거나 <b>목록 복사</b>로 붙여 보내 주세요.</p>
</header>
<main id="grid"></main>
<footer>
  <p class="help">전체 메모(요청 사항 등)</p>
  <textarea id="memo" placeholder="예: 3번째 사진은 보정할 때 밝게 부탁드려요"></textarea>
  <p class="help">고른 사진 목록 — 복사가 안 되면 여기서 직접 복사하세요</p>
  <textarea id="list" readonly></textarea>
</footer>
<div id="viewer" class="hidden">
  <img id="big" alt="">
  <div class="vbar"><span id="vname"></span>
    <input id="vnote" placeholder="이 사진 메모(선택)">
    <button id="vprev">← 이전</button><button id="vpick">고르기</button><button id="vnext">다음 →</button><button id="vclose">닫기</button>
  </div>
</div>
<script>
const DATA = __DATA__;
const KEY = "pixthrough-proof:" + DATA.title + ":" + DATA.photos.length;
let state = { picked: {}, notes: {}, memo: "" };
try { state = Object.assign(state, JSON.parse(localStorage.getItem(KEY) || "{}")); } catch (e) {}
const grid = document.getElementById("grid");
function save() { try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) {} render(); }
function picked() { return DATA.photos.filter(p => state.picked[p.id]); }
function render() {
  const n = picked().length, limit = DATA.limit;
  const count = document.getElementById("count");
  count.textContent = limit ? `${n} / ${limit}장 고름` + (n > limit ? ` (${n - limit}장 넘음)` : "") : `${n}장 고름 · 전체 ${DATA.photos.length}장`;
  count.classList.toggle("over", !!limit && n > limit);
  const only = document.getElementById("onlyPicked").checked;
  for (const card of grid.children) {
    const id = card.dataset.id;
    card.classList.toggle("picked", !!state.picked[id]);
    card.querySelector(".mark").textContent = state.picked[id] ? "✓" : "";
    card.classList.toggle("hidden", only && !state.picked[id]);
  }
  document.getElementById("list").value = listText();
  if (current !== null) showBig(current);
}
function listText() {
  return picked().map(p => p.id + (state.notes[p.id] ? " - " + state.notes[p.id] : "")).join("\\n");
}
function toggle(id) { if (state.picked[id]) delete state.picked[id]; else state.picked[id] = true; save(); }
DATA.photos.forEach((p, i) => {
  const card = document.createElement("div");
  card.className = "card"; card.dataset.id = p.id;
  card.innerHTML = `<img loading="lazy" src="${p.file}" alt=""><div class="mark"></div><div class="name"><a></a><span>${i + 1}</span></div>`;
  card.querySelector(".name a").textContent = p.name;
  card.querySelector("img").onclick = () => toggle(p.id);
  card.querySelector(".mark").onclick = () => toggle(p.id);
  card.querySelector(".name").onclick = () => showBig(i);
  grid.appendChild(card);
});
let current = null;
function showBig(i) {
  current = (i + DATA.photos.length) % DATA.photos.length;
  const p = DATA.photos[current];
  document.getElementById("viewer").classList.remove("hidden");
  document.getElementById("big").src = p.file;
  document.getElementById("vname").textContent = `${current + 1} / ${DATA.photos.length} · ${p.name}` + (state.picked[p.id] ? " · ✓ 고름" : "");
  document.getElementById("vpick").textContent = state.picked[p.id] ? "고르기 취소" : "고르기";
  const note = document.getElementById("vnote");
  if (document.activeElement !== note) note.value = state.notes[p.id] || "";
}
function closeBig() { current = null; document.getElementById("viewer").classList.add("hidden"); }
document.getElementById("vprev").onclick = () => showBig(current - 1);
document.getElementById("vnext").onclick = () => showBig(current + 1);
document.getElementById("vpick").onclick = () => toggle(DATA.photos[current].id);
document.getElementById("vclose").onclick = closeBig;
document.getElementById("vnote").oninput = e => {
  const id = DATA.photos[current].id;
  if (e.target.value.trim()) state.notes[id] = e.target.value.trim(); else delete state.notes[id];
  try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (err) {}
  document.getElementById("list").value = listText();
};
document.addEventListener("keydown", e => {
  if (current === null || e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
  if (e.key === "ArrowLeft") showBig(current - 1);
  else if (e.key === "ArrowRight") showBig(current + 1);
  else if (e.key === " ") { e.preventDefault(); toggle(DATA.photos[current].id); }
  else if (e.key === "Escape") closeBig();
});
document.getElementById("onlyPicked").onchange = render;
const memo = document.getElementById("memo");
memo.value = state.memo || "";
memo.oninput = () => { state.memo = memo.value; try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) {} };
document.getElementById("copy").onclick = async () => {
  const text = listText() + (state.memo ? "\\n\\n메모: " + state.memo : "");
  try { await navigator.clipboard.writeText(text); alert(`${picked().length}장 목록을 복사했습니다. 메신저에 붙여 보내 주세요.`); }
  catch (e) { const list = document.getElementById("list"); list.focus(); list.select(); alert("자동 복사가 안 돼서 아래 목록을 선택해 두었습니다. 복사해서 보내 주세요."); }
};
document.getElementById("save").onclick = () => {
  const result = { version: 1, title: DATA.title, saved: new Date().toISOString(), memo: state.memo || "",
    selected: picked().map(p => ({ id: p.id, name: p.name, note: state.notes[p.id] || "" })) };
  const blob = new Blob([JSON.stringify(result, null, 1)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = "pixthrough-selection.json";  // 한글 이름은 브라우저에 따라 "download"로 바뀌어 영문으로
  document.body.appendChild(a); a.click(); a.remove();
};
render();
</script>
</body>
</html>
"""
