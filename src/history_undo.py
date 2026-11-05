"""작업 기록에서 되돌리기(편의 8, 사용자 결정: 이동 + 이름 바꾸기).

작업 기록(history.jsonl)의 한 줄이 가리키는 자세한 기록 파일을 읽어, 그 작업으로 옮겨지거나 이름이
바뀐 파일을 원래 자리·이름으로 되돌린다. 대상:
- C컷 이동·베스트컷 이동(apply_log_*.jsonl의 move 줄 — 같이 옮긴 RAW 포함)
- 폴더 정리의 RAW·영상·날짜별 이동(organize_log_*.jsonl)
- 시간순 정렬 — 제자리 이름 바꾸기와 한 폴더로 합치기(rename_log_*.jsonl)
복사(원본이 그대로)·삭제·라이트룸 .xmp는 대상이 아니다.

원본 보호: 원래 자리에 다른 파일이 있으면 덮어쓰지 않고 건너뛰고, 옮겨진 파일이 없으면(그 뒤 옮겨졌거나
지워짐) 건너뛴다. 이름이 서로 맞바뀐 경우도 안전하도록 먼저 임시 이름으로 비운 뒤 원래 이름을 붙인다
(시간순 정렬과 같은 2단계). 사진을 되돌리지 못하면 짝 RAW도 그대로 둔다. 되돌린 내역은 기록 파일 옆
`history_undo_log_<시각>.jsonl`과 작업 기록에 남긴다.
"""

from __future__ import annotations

import datetime
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import app_log
import history

log = app_log.get("history_undo")

# 작업 기록 종류 → 기록 파일에서 되돌릴 줄을 읽는 방식
UNDOABLE_KINDS = {
    "C컷 이동": "apply",
    "베스트컷 이동": "apply",
    "폴더 정리": "organize",
    "시간순 정렬": "rename",
}
_ORGANIZE_MOVES = {"move_raw", "move_video", "organize_by_date"}


@dataclass(frozen=True)
class UndoPair:
    current: Path  # 지금 있는 곳(옮긴 뒤·바뀐 이름)
    original: Path  # 되돌릴 곳
    raw_current: Path | None = None
    raw_original: Path | None = None


@dataclass
class UndoPlan:
    kind: str
    log_file: Path
    pairs: list[UndoPair]
    problems: list[tuple[Path, str]] = field(default_factory=list)  # 미리 알 수 있는 건너뛸 것

    @property
    def ready(self) -> list[UndoPair]:
        blocked = {p for p, _why in self.problems}
        return [pair for pair in self.pairs if pair.current not in blocked]


@dataclass
class UndoResult:
    restored: list[UndoPair]
    skipped: list[tuple[Path, str]]
    log_file: Path | None


def can_undo(entry: history.Entry) -> bool:
    return entry.kind in UNDOABLE_KINDS and entry.log_file is not None and entry.log_file.exists()


def _lines(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict):
            rows.append(data)
    return rows


def _opt(value) -> Path | None:
    return Path(value) if value else None


def build_undo_plan(entry: history.Entry) -> UndoPlan:
    """기록 파일을 읽어 되돌릴 목록과 미리 알 수 있는 문제(원래 자리 막힘·파일 없음)를 만든다."""
    if not can_undo(entry):
        raise ValueError("이 작업은 되돌릴 수 없습니다(복사·삭제·라이트룸이거나 기록 파일이 없음).")
    style = UNDOABLE_KINDS[entry.kind]
    pairs: list[UndoPair] = []
    for row in _lines(entry.log_file):
        if row.get("error"):
            continue
        if style == "apply" and row.get("action") == "move" and row.get("destination"):
            pairs.append(UndoPair(Path(row["destination"]), Path(row["source"]),
                                  _opt(row.get("raw_destination")), _opt(row.get("raw_source"))))
        elif style == "organize" and row.get("action") in _ORGANIZE_MOVES:
            pairs.append(UndoPair(Path(row["new_path"]), Path(row["old_path"])))
        elif style == "rename" and row.get("new_path") and row.get("old_path"):
            pairs.append(UndoPair(Path(row["new_path"]), Path(row["old_path"]),
                                  _opt(row.get("raw_new_path")), _opt(row.get("raw_old_path"))))
    plan = UndoPlan(entry.kind, entry.log_file, pairs)
    currents = {p.current for p in pairs} | {p.raw_current for p in pairs if p.raw_current}
    for pair in pairs:
        if not pair.current.exists():
            plan.problems.append((pair.current, "옮긴 파일이 없음(그 뒤 옮겨졌거나 지워짐)"))
        elif pair.original.exists() and pair.original not in currents:
            plan.problems.append((pair.current, f"원래 자리에 다른 파일이 있음: {pair.original.name}"))
    return plan


def _temp_name(path: Path, index: int) -> Path:
    return path.with_name(f".pixthrough-undo-{index}{path.suffix}")


def apply_undo(plan: UndoPlan) -> UndoResult:
    """되돌린다. 1단계: 되돌릴 파일을 같은 폴더 안 임시 이름으로, 2단계: 원래 자리로(막혀 있으면 지금
    이름으로 돌려놓고 건너뜀)."""
    restored: list[UndoPair] = []
    skipped: list[tuple[Path, str]] = list(plan.problems)
    staged: list[tuple[UndoPair, Path, Path | None]] = []
    for index, pair in enumerate(plan.ready):
        try:
            temp = _temp_name(pair.current, index)
            pair.current.rename(temp)
        except OSError as e:
            skipped.append((pair.current, str(e)))
            continue
        raw_temp = None
        if pair.raw_current is not None and pair.raw_original is not None and pair.raw_current.exists():
            try:
                raw_temp = _temp_name(pair.raw_current, index)
                pair.raw_current.rename(raw_temp)
            except OSError:
                raw_temp = None
        staged.append((pair, temp, raw_temp))

    def place(temp: Path, original: Path, current: Path) -> str | None:
        if original.exists():
            temp.rename(current)
            return f"원래 자리에 다른 파일이 있음: {original.name}"
        try:
            original.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(temp), str(original))
        except OSError as e:
            if temp.exists():
                temp.rename(current)
            return str(e)
        return None

    for pair, temp, raw_temp in staged:
        problem = place(temp, pair.original, pair.current)
        if problem is not None:
            skipped.append((pair.current, problem))
            if raw_temp is not None:
                raw_temp.rename(pair.raw_current)  # 사진을 못 되돌리면 RAW도 그대로
            continue
        if raw_temp is not None:
            raw_problem = place(raw_temp, pair.raw_original, pair.raw_current)
            if raw_problem is not None:
                skipped.append((pair.raw_current, raw_problem))
        restored.append(pair)

    log_path = plan.log_file.parent / f"history_undo_log_{datetime.datetime.now():%Y%m%d_%H%M%S}.jsonl"
    try:
        with open(log_path, "w", encoding="utf-8") as out:
            for pair in restored:
                out.write(json.dumps({"action": "undo", "from": str(pair.current), "to": str(pair.original),
                                      "raw_from": str(pair.raw_current) if pair.raw_current else None,
                                      "raw_to": str(pair.raw_original) if pair.raw_original else None},
                                     ensure_ascii=False) + "\n")
            for path, why in skipped:
                out.write(json.dumps({"action": "skip", "path": str(path), "reason": why}, ensure_ascii=False) + "\n")
    except OSError:
        log_path = None
    log.info("기록에서 되돌리기(%s): %d개 복원, %d개 건너뜀", plan.kind, len(restored), len(skipped))
    history.record(
        "기록에서 되돌리기",
        f"{plan.kind} {len(restored)}개를 원래대로" + (f" ({len(skipped)}개 건너뜀)" if skipped else ""),
        restored[0].original.parent if restored else plan.log_file.parent, log_path,
    )
    return UndoResult(restored, skipped, log_path)
