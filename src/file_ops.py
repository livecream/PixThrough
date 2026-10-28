"""M6: 실제 파일 복사/이동 + 로그.

PROJECT_SPEC.md 4.5/7절: 원본 삭제 코드는 두지 않는다. 기본 동작은 복사(copy)이며
move=True를 명시적으로 넘겼을 때만 이동한다. 파일명이 이미 존재하면 덮어쓰지 않고
번호를 붙인다. 모든 처리 내역은 타임스탬프가 포함된 로그 파일(JSON Lines)로 남긴다.
"""

from __future__ import annotations

import datetime
import glob
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from raw_preview import is_raw
from report import ReportRow
from scanner import RAW_EXTENSIONS
import app_log

log = app_log.get("files")


@dataclass(frozen=True)
class FileOpResult:
    source: Path
    destination: Path
    raw_source: Path | None
    raw_destination: Path | None
    action: str  # "copy" | "move"


class ApplyError(RuntimeError):
    """일부 파일 작업이 실패했다. results에는 그 전에 성공한 것이 들어 있어, 이동이었다면
    이미 옮긴 파일을 되돌릴 수 있다(undo_moves)."""

    def __init__(self, message: str, results: list[FileOpResult]) -> None:
        super().__init__(message)
        self.results = results


@dataclass(frozen=True)
class UndoOutcome:
    restored: list[FileOpResult]  # 사진(과 RAW)을 원래 자리로 돌려놓은 것
    skipped: list[tuple[Path, str]]  # 돌려놓지 못한 파일과 이유


def _raw_candidates(directory: Path, stem: str, raw_exts_lower: set[str]) -> list[Path]:
    return sorted(
        p
        for p in directory.glob(glob.escape(stem) + ".*")
        if p.is_file() and p.suffix.lower() in raw_exts_lower
    )


def find_matching_raw(photo_path: Path) -> Path | None:
    """베이스 파일명이 같은 RAW 파일을 찾는다 (없으면 None).

    확장자는 대소문자 구분 없이 비교하되(RAW는 보통 .CR3처럼 대문자), 실제 디스크에
    있는 파일명을 그대로 반환한다. photo_path.with_suffix(ext)로 소문자 확장자를
    조립해 반환하면 macOS/Windows(대소문자 무시 파일시스템)에서는 우연히 exists()가
    통과해도 원본과 다른 케이스의 이름이 되어, 복사 시 확장자가 바뀌거나 Linux
    같은 대소문자 구분 파일시스템에서는 아예 못 찾는 문제가 생긴다.

    먼저 사진과 같은 폴더(카메라 바디 덤프 폴더 등)에서 찾고, 없으면 사진 폴더
    바로 아래의 RAW/ 서브폴더를 본다 — apply_selection이 만드는
    "보정 진행 예정/RAW/" 레이아웃(PROJECT_SPEC.md)에서도 짝을 찾을 수 있게 하기
    위함이다.
    """
    photo_path = Path(photo_path)
    raw_exts_lower = {ext.lower() for ext in RAW_EXTENSIONS}

    same_dir = _raw_candidates(photo_path.parent, photo_path.stem, raw_exts_lower)
    if same_dir:
        return same_dir[0]

    raw_subdir = _raw_candidates(photo_path.parent / "RAW", photo_path.stem, raw_exts_lower)
    return raw_subdir[0] if raw_subdir else None


def unique_destination(dest_dir: Path, filename: str) -> Path:
    """dest_dir/filename이 이미 있으면 덮어쓰지 않고 " (n)"을 붙여 빈 경로를 찾는다."""
    dest_dir = Path(dest_dir)
    candidate = dest_dir / filename
    if not candidate.exists():
        return candidate

    stem, suffix = candidate.stem, candidate.suffix
    n = 1
    while True:
        candidate = dest_dir / f"{stem} ({n}){suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def _guard_against_overlapping_output(rows: list[ReportRow], output_dir: Path) -> None:
    """output_dir가 선택된 원본의 폴더와 같거나 서로 포함 관계면 중단한다.

    같은 폴더를 입력/출력으로 고르거나(복사 시 "name (1).jpg"로 원본 옆에 중복 생성,
    이동 시 원본 자리 이름 변경) 출력 폴더가 입력 폴더 안에 중첩된 경우(예: 입력
    ~/Photos/shoot, 출력 ~/Photos/shoot/보정대기) 둘 다 원본 폴더가 뒤섞일 수 있어
    파일 작업을 시작하기 전에 막는다.
    """
    resolved_output = output_dir.resolve()
    for row in rows:
        source_parent = Path(row.path).resolve().parent
        overlaps = (
            resolved_output == source_parent
            or resolved_output in source_parent.parents
            or source_parent in resolved_output.parents
        )
        if overlaps:
            raise ValueError(
                "출력 폴더가 입력 폴더와 같거나 그 안에 포함되어 있습니다: "
                "원본이 뒤섞일 수 있어 중단합니다. "
                "출력 폴더를 원본과 겹치지 않는 곳으로 지정하세요."
            )


def apply_selection(
    rows: list[ReportRow],
    output_dir: Path,
    move: bool = False,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[FileOpResult]:
    """선택된(selected=True) 사진을 output_dir로 복사(기본) 또는 이동한다.

    같은 베이스 파일명의 RAW가 있으면 output_dir/RAW/에도 함께 옮긴다. 짝 JPG 없이 RAW
    자체가 선택된 경우(RAW만 찍은 폴더)는 그 RAW를 output_dir/RAW/로 옮긴다. 처리 내역은
    output_dir/apply_log_<타임스탬프>.jsonl에 한 줄씩 기록한다. 실패가 하나라도 있으면
    (복사 누락 감지) 예외를 던져 사용자가 알 수 있게 한다. on_progress가 주어지면
    선택된 사진을 하나 처리할 때마다(성공/실패 무관) (완료한 수, 전체 수)를 알려준다.
    """
    output_dir = Path(output_dir)
    selected_rows = [r for r in rows if r.selected]
    _guard_against_overlapping_output(selected_rows, output_dir)

    output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = output_dir / "RAW"
    total = len(selected_rows)
    transfer = shutil.move if move else shutil.copy2
    action = "move" if move else "copy"

    results: list[FileOpResult] = []
    errors: list[tuple[Path, str]] = []
    log_path = output_dir / f"apply_log_{datetime.datetime.now():%Y%m%d_%H%M%S}.jsonl"

    with open(log_path, "w", encoding="utf-8") as log_file:
        for index, row in enumerate(selected_rows, start=1):
            source = Path(row.path)
            try:
                if is_raw(source):
                    # RAW만 있는 사진: 짝이 따로 없고, 결과 폴더 레이아웃은 JPG+RAW와 같게
                    raw_source = None
                    raw_dir.mkdir(parents=True, exist_ok=True)
                    dest = unique_destination(raw_dir, source.name)
                else:
                    raw_source = find_matching_raw(source)
                    dest = unique_destination(output_dir, source.name)
                transfer(str(source), str(dest))

                raw_dest = None
                if raw_source is not None:
                    raw_dir.mkdir(parents=True, exist_ok=True)
                    raw_dest = unique_destination(raw_dir, raw_source.name)
                    transfer(str(raw_source), str(raw_dest))

                results.append(
                    FileOpResult(
                        source=source,
                        destination=dest,
                        raw_source=raw_source,
                        raw_destination=raw_dest,
                        action=action,
                    )
                )
                log_file.write(
                    json.dumps(
                        {
                            "timestamp": datetime.datetime.now().isoformat(),
                            "action": action,
                            "source": str(source),
                            "destination": str(dest),
                            "raw_source": str(raw_source) if raw_source else None,
                            "raw_destination": str(raw_dest) if raw_dest else None,
                            "reason": row.reason,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
            except OSError as e:
                errors.append((source, str(e)))
                log_file.write(
                    json.dumps(
                        {
                            "timestamp": datetime.datetime.now().isoformat(),
                            "action": action,
                            "source": str(source),
                            "error": str(e),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
            finally:
                if on_progress is not None:
                    on_progress(index, total)

    log.info("%s: %d장 → %s (실패 %d)", "이동" if move else "복사", len(results), output_dir, len(errors))
    for source, error in errors:
        log.warning("%s 실패: %s — %s", "이동" if move else "복사", source, error)
    if errors:
        verb = "옮기지" if move else "복사하지"
        details = "\n".join(f"{source.name}: {error}" for source, error in errors)
        raise ApplyError(
            f"{len(errors)}개 파일을 {verb} 못했습니다 (나머지 {len(results)}개는 완료, 기록: {log_path.name}).\n"
            + details,
            results,
        )

    return results


def _move_back(moved_to: Path, original: Path) -> str | None:
    """옮긴 파일을 원래 자리로 되돌린다. 못 하면 이유를 돌려준다(원래 자리에 다른 파일이
    생겼으면 덮어쓰지 않는다)."""
    if original.exists():
        return "원래 자리에 같은 이름의 파일이 있어 덮어쓰지 않음"
    if not moved_to.exists():
        return "옮긴 파일을 찾을 수 없음(그 사이 옮겨졌거나 지워짐)"
    try:
        original.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(moved_to), str(original))
    except OSError as e:
        return str(e)
    return None


def undo_moves(results: list[FileOpResult], log_dir: Path) -> UndoOutcome:
    """apply_selection(move=True)로 옮긴 사진(과 같이 옮긴 RAW)을 원래 폴더로 되돌린다.

    복사(copy)한 것은 원본이 그대로 있어 대상이 아니다(복사본을 지우지 않는다). 되돌린
    내역은 log_dir/undo_log_<타임스탬프>.jsonl에 남긴다. 사진을 못 되돌리면 그 RAW도
    그대로 둔다(짝이 서로 다른 폴더로 갈라지지 않게).
    """
    restored: list[FileOpResult] = []
    skipped: list[tuple[Path, str]] = []
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"undo_log_{datetime.datetime.now():%Y%m%d_%H%M%S}.jsonl"
    with open(log_path, "w", encoding="utf-8") as log_file:
        for result in reversed(results):
            if result.action != "move":
                continue
            entry = {
                "timestamp": datetime.datetime.now().isoformat(),
                "action": "undo_move",
                "from": str(result.destination),
                "to": str(result.source),
            }
            problem = _move_back(result.destination, result.source)
            if problem is None and result.raw_source is not None and result.raw_destination is not None:
                raw_problem = _move_back(result.raw_destination, result.raw_source)
                entry["raw_from"] = str(result.raw_destination)
                entry["raw_to"] = str(result.raw_source)
                if raw_problem is not None:
                    entry["raw_error"] = raw_problem
                    skipped.append((result.raw_destination, raw_problem))
            if problem is None:
                restored.append(result)
            else:
                entry["error"] = problem
                skipped.append((result.destination, problem))
            log_file.write(json.dumps(entry, ensure_ascii=False) + "\n")
    restored.reverse()
    log.info("이동 되돌리기: %d장 복원, %d개 건너뜀 (기록 %s)", len(restored), len(skipped), log_path)
    for path, why in skipped:
        log.warning("되돌리지 못함: %s — %s", path, why)
    return UndoOutcome(restored=restored, skipped=skipped)
