"""라이트룸 별점 쓰기: 베스트컷은 별점, C컷은 거부 표시를 RAW 옆 .xmp(사이드카)에 기록한다.

사용자 결정(PROJECT_SPEC 8절):
- 라이트룸은 RAW만 옆의 .xmp를 읽으므로 RAW에만 쓴다. RAW 파일 자체는 건드리지 않고,
  JPG만 있는 사진은 건너뛰고 알려준다(JPG 원본을 고치지 않는다).
- 같은 이름의 .xmp가 이미 있으면(라이트룸에서 보정한 기록 등) 다른 내용은 그대로 두고
  별점·거부 칸만 바꾼다. 바꾸기 전 파일은 백업 폴더에 복사해 둔다.
- 베스트컷 = 사용자가 정한 별점(xmp:Rating 1~5). 예전에 거부 표시(xmpDM:pick = -1)가
  있었으면 0(표시 없음)으로 되돌린다. C컷 = 거부: Lightroom Classic 13.2부터 쓰는
  xmpDM:pick = -1과, Bridge 등이 거부로 쓰는 xmp:Rating = -1을 함께 쓴다.

기존 .xmp는 XML로 다시 써서 모양을 바꾸지 않도록 글자 그대로 고친다(필요한 칸만 바꾸거나
첫 rdf:Description에 넣음). 고친 결과가 XML로 읽히고 원하는 값이 들어갔는지 확인한 뒤에만
쓴다. 처리 기록(xmp_log.jsonl)과 백업은 run 폴더 하나에 모아, 마지막 쓰기를 되돌릴 수
있다(undo_xmp: 그 뒤로 바뀌지 않은 파일만).
"""

from __future__ import annotations

import datetime
import glob
import hashlib
import json
import os
import re
import shutil
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from file_ops import find_matching_raw
from raw_preview import is_raw

RATING_MIN, RATING_MAX = 1, 5
MARK_BEST = "best"
MARK_REJECT = "reject"

ACTION_CREATED = "created"
ACTION_UPDATED = "updated"
ACTION_SKIPPED = "skipped"

NS_XMP = "http://ns.adobe.com/xap/1.0/"
NS_XMPDM = "http://ns.adobe.com/xmp/1.0/DynamicMedia/"
NS_RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
_PREFIXES = {"xmp": NS_XMP, "xmpDM": NS_XMPDM}


class UnsupportedXmp(ValueError):
    """이 .xmp는 안전하게 고칠 수 없다(구조가 다르거나 접두어가 다른 뜻으로 쓰임)."""


@dataclass(frozen=True)
class XmpPlanRow:
    photo: Path  # 베스트컷 화면의 사진(JPG 또는 RAW만 있는 사진의 RAW)
    raw: Path | None  # 별점을 붙일 RAW(없으면 건너뜀)
    xmp: Path | None  # 쓸 .xmp 경로
    mark: str  # MARK_BEST | MARK_REJECT
    existing: bool  # 이미 .xmp가 있음(고침)


@dataclass(frozen=True)
class XmpResult:
    plan: XmpPlanRow
    action: str  # ACTION_CREATED | ACTION_UPDATED | ACTION_SKIPPED
    reason: str | None = None  # 건너뛴 이유
    backup: Path | None = None  # 고치기 전 파일의 백업
    written_hash: str | None = None  # 우리가 쓴 내용(되돌리기 전에 그 뒤로 바뀌었는지 확인)


def _existing_sidecar(raw: Path) -> Path | None:
    """RAW 옆의 같은 이름 .xmp(대소문자 무시)."""
    for candidate in sorted(raw.parent.glob(glob.escape(raw.stem) + ".*")):
        if candidate.suffix.lower() == ".xmp" and candidate.is_file():
            return candidate
    return None


def plan_xmp(best: list[Path], rejects: list[Path]) -> list[XmpPlanRow]:
    """베스트컷·C컷 사진마다 별점을 붙일 RAW와 .xmp 경로를 정한다(아직 쓰지 않음)."""
    rows = []
    for mark, photos in ((MARK_BEST, best), (MARK_REJECT, rejects)):
        for photo in photos:
            raw = photo if is_raw(photo) else find_matching_raw(photo)
            if raw is None:
                rows.append(XmpPlanRow(photo, None, None, mark, False))
                continue
            existing = _existing_sidecar(raw)
            xmp = existing if existing is not None else raw.with_suffix(".xmp")
            rows.append(XmpPlanRow(photo, raw, xmp, mark, existing is not None))
    return rows


def _marks(mark: str, rating: int, current_pick: int | None) -> dict[tuple[str, str], int | None]:
    """(접두어, 이름) → 넣을 값. None이면 손대지 않는다."""
    if mark == MARK_REJECT:
        return {("xmp", "Rating"): -1, ("xmpDM", "pick"): -1}
    # 베스트컷: 별점. 예전에 거부였으면 거부 표시를 지운다(0 = 표시 없음)
    return {("xmp", "Rating"): rating, ("xmpDM", "pick"): 0 if current_pick == -1 else None}


def new_sidecar(values: dict[tuple[str, str], int | None]) -> str:
    attributes = "".join(
        f'\n    {prefix}:{name}="{value}"' for (prefix, name), value in values.items() if value is not None
    )
    return (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="PixThrough">\n'
        f' <rdf:RDF xmlns:rdf="{NS_RDF}">\n'
        '  <rdf:Description rdf:about=""\n'
        f'    xmlns:xmp="{NS_XMP}"\n'
        f'    xmlns:xmpDM="{NS_XMPDM}"{attributes}/>\n'
        " </rdf:RDF>\n"
        "</x:xmpmeta>\n"
    )


def _read_values(text: str) -> dict[tuple[str, str], str]:
    """XML로 읽어 rdf:Description들에 있는 xmp:Rating·xmpDM:pick 값을 모은다(속성·요소 모두)."""
    root = ET.fromstring(text.encode("utf-8"))
    values: dict[tuple[str, str], str] = {}
    for description in root.iter(f"{{{NS_RDF}}}Description"):
        for prefix, namespace in _PREFIXES.items():
            for name in ("Rating", "pick"):
                tag = f"{{{namespace}}}{name}"
                if tag in description.attrib:
                    values[(prefix, name)] = description.attrib[tag]
                child = description.find(tag)
                if child is not None and child.text is not None:
                    values[(prefix, name)] = child.text.strip()
    return values


def current_pick(text: str) -> int | None:
    try:
        value = _read_values(text).get(("xmpDM", "pick"))
        return int(value) if value is not None else None
    except (ET.ParseError, ValueError):
        return None


def set_values(text: str, values: dict[tuple[str, str], int | None]) -> str:
    """기존 .xmp 글자에서 해당 칸만 바꾸거나 첫 rdf:Description에 넣는다. 결과를 XML로 다시
    읽어 값이 맞는지 확인하고, 안전하게 고칠 수 없으면 UnsupportedXmp."""
    try:
        _read_values(text)
    except ET.ParseError as e:
        raise UnsupportedXmp(f"XML로 읽을 수 없음: {e}") from e
    for (prefix, name), value in values.items():
        if value is None:
            continue
        namespace = _PREFIXES[prefix]
        declared = re.search(rf'xmlns:{prefix}\s*=\s*(["\'])(.*?)\1', text)
        if declared is not None and declared.group(2) != namespace:
            raise UnsupportedXmp(f"{prefix} 접두어가 다른 뜻({declared.group(2)})으로 쓰임")
        attribute = re.compile(rf'(\b{prefix}:{name}\s*=\s*)(["\'])(.*?)\2')
        element = re.compile(rf"(<{prefix}:{name}>)(.*?)(</{prefix}:{name}>)", re.DOTALL)
        if attribute.search(text):
            text = attribute.sub(lambda m: f'{m.group(1)}{m.group(2)}{value}{m.group(2)}', text)
        elif element.search(text):
            text = element.sub(lambda m: f"{m.group(1)}{value}{m.group(3)}", text)
        else:
            start = re.search(r"<rdf:Description\b", text)
            if start is None:
                raise UnsupportedXmp("rdf:Description이 없음")
            insert = f' {prefix}:{name}="{value}"'
            if declared is None:
                insert = f' xmlns:{prefix}="{namespace}"' + insert
            text = text[: start.end()] + insert + text[start.end():]
    try:
        found = _read_values(text)
    except ET.ParseError as e:
        raise UnsupportedXmp(f"고친 결과가 XML로 읽히지 않음: {e}") from e
    for key, value in values.items():
        if value is not None and found.get(key) != str(value):
            raise UnsupportedXmp(f"{key[0]}:{key[1]} 값을 확인할 수 없음")
    return text


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".pixthrough-tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def write_xmp(rows: list[XmpPlanRow], rating: int, run_dir: Path) -> list[XmpResult]:
    """계획대로 .xmp를 만들거나 고친다. 고치기 전 파일은 run_dir/backup/에, 처리 기록은
    run_dir/xmp_log.jsonl에. 한 장이 실패해도 나머지는 계속한다."""
    if not RATING_MIN <= rating <= RATING_MAX:
        raise ValueError(f"별점은 {RATING_MIN}~{RATING_MAX} 사이여야 합니다.")
    run_dir = Path(run_dir)
    backup_dir = run_dir / "backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    results: list[XmpResult] = []
    with open(run_dir / "xmp_log.jsonl", "w", encoding="utf-8") as log_file:
        for index, row in enumerate(rows, start=1):
            result = _write_one(row, rating, backup_dir, index)
            results.append(result)
            log_file.write(
                json.dumps(
                    {
                        "timestamp": datetime.datetime.now().isoformat(),
                        "photo": str(row.photo),
                        "xmp": str(row.xmp) if row.xmp else None,
                        "mark": row.mark,
                        "rating": rating if row.mark == MARK_BEST else -1,
                        "action": result.action,
                        "reason": result.reason,
                        "backup": str(result.backup) if result.backup else None,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    return results


def _write_one(row: XmpPlanRow, rating: int, backup_dir: Path, index: int) -> XmpResult:
    if row.raw is None or row.xmp is None:
        return XmpResult(row, ACTION_SKIPPED, "RAW 없음(라이트룸은 JPG 옆 .xmp를 읽지 않음)")
    try:
        if row.xmp.exists():
            original = row.xmp.read_bytes()
            text = original.decode("utf-8")
            new_text = set_values(text, _marks(row.mark, rating, current_pick(text)))
            data = new_text.encode("utf-8")
            if data == original:
                return XmpResult(row, ACTION_UPDATED, "이미 같은 값", written_hash=_hash(data))
            backup = backup_dir / f"{index:05d}_{row.xmp.name}"
            shutil.copy2(row.xmp, backup)
            _write_atomic(row.xmp, data)
            return XmpResult(row, ACTION_UPDATED, backup=backup, written_hash=_hash(data))
        data = new_sidecar(_marks(row.mark, rating, None)).encode("utf-8")
        _write_atomic(row.xmp, data)
        return XmpResult(row, ACTION_CREATED, written_hash=_hash(data))
    except UnsupportedXmp as e:
        return XmpResult(row, ACTION_SKIPPED, f"기존 .xmp를 안전하게 고칠 수 없음: {e}")
    except (OSError, UnicodeDecodeError) as e:
        return XmpResult(row, ACTION_SKIPPED, str(e))


def undo_xmp(results: list[XmpResult]) -> tuple[int, list[tuple[Path, str]]]:
    """마지막 쓰기를 되돌린다: 새로 만든 .xmp는 지우고, 고친 .xmp는 백업으로 되돌린다.
    그 뒤로 내용이 바뀐 파일(라이트룸이 다시 쓴 경우 등)은 건드리지 않는다.
    돌려준 값: (되돌린 수, [(파일, 못 되돌린 이유)])."""
    restored = 0
    skipped: list[tuple[Path, str]] = []
    for result in results:
        xmp = result.plan.xmp
        if xmp is None or result.action == ACTION_SKIPPED or result.written_hash is None:
            continue
        if result.action == ACTION_UPDATED and result.backup is None:
            continue  # 이미 같은 값이라 바꾸지 않았다
        try:
            if not xmp.exists() or _hash(xmp.read_bytes()) != result.written_hash:
                skipped.append((xmp, "그 뒤로 내용이 바뀌어 그대로 둠"))
                continue
            if result.action == ACTION_CREATED:
                xmp.unlink()  # 이번에 새로 만든 파일만(원래 없던 파일)
            else:
                _write_atomic(xmp, result.backup.read_bytes())
            restored += 1
        except OSError as e:
            skipped.append((xmp, str(e)))
    return restored, skipped


def default_run_dir(app_data_dir: Path) -> Path:
    return Path(app_data_dir) / "xmp_backup" / f"{datetime.datetime.now():%Y%m%d_%H%M%S}"
