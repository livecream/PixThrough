"""M9: 여러 바디의 사진(+영상)을 촬영 시각순으로 정렬해 파일명을 00001부터 순서대로 바꾼다.

두 가지 모드가 있다:

- 제자리 리네임(build_rename_plan/apply_rename_plan): CLAUDE.md 원칙(원본 보호)에
  따라 파일을 다른 곳으로 옮기지는 않고, 각 파일을 원래 있던 폴더에서 이름만
  바꾼다. 여러 바디 폴더를 그대로 두고 싶을 때 쓴다.
- 한 폴더로 병합(build_merge_plan/apply_merge_plan): 여러 바디 폴더에 흩어진
  파일을 사용자가 지정한 출력 폴더 하나로 모아 옮기면서 새 번호를 붙인다.
  전달용으로 한 폴더에 다 모으고 싶을 때 쓴다 — 원본 바디 폴더에서는 파일이
  빠지므로(이동) 병합 여부는 사용자가 명시적으로 선택해야 한다.

두 모드 모두 촬영 시각 기준으로 전체를 한 줄로 합쳐 정렬하고, 같은 베이스
파일명의 RAW가 있으면 짝지어 같은 번호를 부여한다.

영상 파일(VIDEO_EXTENSIONS)은 이 리네임 기능에서만 함께 다룬다 — 화질 점수
(선명도/노출/얼굴)를 매기는 베스트컷/C컷 판정은 사진 전용이라 영상에는 의미가
없기 때문. 영상은 EXIF가 없어 파일 수정 시각(mtime)을 촬영 시각으로 쓴다(사진에서
EXIF가 없을 때 mtime으로 폴백하는 것과 동일한 방식). RAW 짝짓기 대상도 아니다.

리네임은 실수로 파일을 덮어써 잃어버리면 되돌릴 수 없으므로, 실행 전 전수
충돌 검사를 하고 실제 적용은 임시 이름을 거치는 2단계로 수행해 이번 배치 안에서
새 이름이 서로 겹치는 경우에도 데이터 손실 없이 처리한다.
"""

from __future__ import annotations

import datetime
import json
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from clock_sync import camera_keys, shifted
from file_ops import find_matching_raw
from scanner import PhotoMetadata, extract_metadata, scan_folder
import app_log

log = app_log.get("rename")

DEFAULT_DIGITS = 5
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mts", ".m4v"}


@dataclass(frozen=True)
class RenamePlanRow:
    seq: int
    old_path: Path
    new_path: Path
    raw_old_path: Path | None
    raw_new_path: Path | None
    datetime_original: datetime.datetime  # 바디 시차를 더한 시각(정렬 기준)
    clock_offset: float = 0.0  # 이 파일 바디에 더한 시차(초, clock_sync)


@dataclass(frozen=True)
class RenameResult:
    old_path: Path
    new_path: Path
    raw_old_path: Path | None
    raw_new_path: Path | None


def _scan_video_files(input_dir: Path, recursive: bool) -> list[Path]:
    pattern_iter = input_dir.rglob("*") if recursive else input_dir.glob("*")
    return sorted(
        p for p in pattern_iter if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
    )


def _stat_key(path: Path) -> tuple[float, int]:
    stat = path.stat()
    return stat.st_mtime, stat.st_size


def _extract_metadata_cached(
    path: Path, cache: dict[Path, tuple[float, int, PhotoMetadata]] | None
) -> PhotoMetadata:
    """cache가 주어지면 파일의 (수정 시각, 크기)가 이전과 같을 때 EXIF를 다시
    읽지 않고 캐시된 메타데이터(촬영 시각·카메라)를 재사용한다."""
    if cache is None:
        return extract_metadata(path)
    key = _stat_key(path)
    cached = cache.get(path)
    if cached is not None and cached[:2] == key:
        return cached[2]
    meta = extract_metadata(path)
    cache[path] = (*key, meta)
    return meta


def _scan_and_number(
    input_dirs: list[Path],
    recursive: bool,
    on_progress: Callable[[int, int, Path], None] | None = None,
    cache: dict[Path, tuple[float, int, PhotoMetadata]] | None = None,
    offsets: dict[str, float] | None = None,
) -> list[tuple[int, Path, Path | None, datetime.datetime, float]]:
    """여러 폴더의 사진+영상을 촬영 시각순으로 합쳐 정렬하고 순번을 매긴다.

    동일 촬영 시각(초 단위까지만 있는 EXIF 특성상 서로 다른 바디가 같은 초에 찍을 수
    있음)이 겹치면 원본 경로 문자열 순서로 동점을 처리해 실행할 때마다 결과가
    같도록 한다. 반환값은 (순번, 원본 경로, 짝지어진 RAW 경로 또는 None, 촬영 시각)
    튜플 목록 — 제자리 리네임/병합 두 모드 모두 이 순서/짝짓기를 그대로 쓴다.

    on_progress가 주어지면 파일 1개의 촬영 시각을 읽을 때마다 (완료한 수, 전체 수,
    방금 읽은 파일 경로)를 알려준다. offsets(바디 이름 → 시차 초, clock_sync)가 있으면
    바디마다 시차를 더한 시각으로 정렬한다(영상은 같은 폴더 사진의 바디를 따름).
    """
    all_files: list[Path] = []
    for input_dir in input_dirs:
        input_dir = Path(input_dir)
        all_files.extend(scan_folder(input_dir, recursive=recursive))
        all_files.extend(_scan_video_files(input_dir, recursive=recursive))

    total = len(all_files)
    metas: list[tuple[Path, PhotoMetadata | None, datetime.datetime]] = []
    for i, path in enumerate(all_files, start=1):
        if path.suffix.lower() in VIDEO_EXTENSIONS:
            metas.append((path, None, datetime.datetime.fromtimestamp(path.stat().st_mtime)))
        else:
            meta = _extract_metadata_cached(path, cache)
            metas.append((path, meta, meta.datetime_original))
        if on_progress is not None:
            on_progress(i, total, path)

    offsets = offsets or {}
    keys = camera_keys([(path, meta) for path, meta, _dt in metas]) if any(offsets.values()) else {}
    items: list[tuple[Path, datetime.datetime, float]] = []
    for path, _meta, dt in metas:
        offset = offsets.get(keys.get(path), 0.0) if keys.get(path) is not None else 0.0
        items.append((path, shifted(dt, keys.get(path), offsets), offset))
    items.sort(key=lambda item: (item[1], str(item[0])))

    numbered = []
    for seq, (path, dt, offset) in enumerate(items, start=1):
        is_video = path.suffix.lower() in VIDEO_EXTENSIONS
        raw = None if is_video else find_matching_raw(path)
        numbered.append((seq, path, raw, dt, offset))
    return numbered


def build_rename_plan(
    input_dirs: list[Path],
    recursive: bool = False,
    digits: int = DEFAULT_DIGITS,
    on_progress: Callable[[int, int, Path], None] | None = None,
    cache: dict | None = None,
    offsets: dict[str, float] | None = None,
) -> list[RenamePlanRow]:
    """여러 입력 폴더의 사진+영상을 촬영 시각순으로 합쳐 정렬하고, 각자 원래 폴더에서
    제자리로 리네임할 계획을 만든다 (원본 폴더는 그대로, 이름만 바뀜)."""
    rows: list[RenamePlanRow] = []
    for seq, path, raw, dt, offset in _scan_and_number(
        input_dirs, recursive, on_progress=on_progress, cache=cache, offsets=offsets
    ):
        stem = f"{seq:0{digits}d}"
        new_path = path.with_name(f"{stem}{path.suffix}")
        raw_new = raw.with_name(f"{stem}{raw.suffix}") if raw else None
        rows.append(
            RenamePlanRow(
                seq=seq,
                old_path=path,
                new_path=new_path,
                raw_old_path=raw,
                raw_new_path=raw_new,
                datetime_original=dt,
                clock_offset=offset,
            )
        )
    return rows


def build_merge_plan(
    input_dirs: list[Path],
    output_dir: Path,
    recursive: bool = False,
    digits: int = DEFAULT_DIGITS,
    on_progress: Callable[[int, int, Path], None] | None = None,
    cache: dict | None = None,
    offsets: dict[str, float] | None = None,
) -> list[RenamePlanRow]:
    """여러 입력 폴더의 사진+영상을 촬영 시각순으로 합쳐 정렬하고, 하나의 출력
    폴더로 모아 옮길 계획을 만든다. RAW는 output_dir/RAW/에 모인다 (apply_selection이
    베스트컷을 복사할 때 쓰는 것과 같은 레이아웃)."""
    output_dir = Path(output_dir)
    rows: list[RenamePlanRow] = []
    for seq, path, raw, dt, offset in _scan_and_number(
        input_dirs, recursive, on_progress=on_progress, cache=cache, offsets=offsets
    ):
        stem = f"{seq:0{digits}d}"
        new_path = output_dir / f"{stem}{path.suffix}"
        raw_new = (output_dir / "RAW" / f"{stem}{raw.suffix}") if raw else None
        rows.append(
            RenamePlanRow(
                seq=seq,
                old_path=path,
                new_path=new_path,
                raw_old_path=raw,
                raw_new_path=raw_new,
                datetime_original=dt,
                clock_offset=offset,
            )
        )
    return rows


def _row_to_report_dict(row: RenamePlanRow) -> dict:
    return {
        "seq": row.seq,
        "old_path": str(row.old_path),
        "new_path": str(row.new_path),
        "raw_old_path": str(row.raw_old_path) if row.raw_old_path else None,
        "raw_new_path": str(row.raw_new_path) if row.raw_new_path else None,
        "datetime_original": row.datetime_original.isoformat(),
        "clock_offset_seconds": row.clock_offset,
    }


def write_rename_report(rows: list[RenamePlanRow], output_path: Path) -> None:
    """리네임 계획을 JSON으로 저장한다 (실제 적용 전 사람이 먼저 확인하는 dry-run용)."""
    output_path = Path(output_path)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump([_row_to_report_dict(r) for r in rows], f, ensure_ascii=False, indent=2)


def _check_collisions(pairs: list[tuple[Path, Path]]) -> None:
    """이번 배치 밖의 파일과 새 이름이 겹치면 아무것도 건드리기 전에 중단한다.

    배치 안에서 새 이름끼리 겹치는 것(예: 원본 이름 중 하나가 다른 파일의 목표
    이름과 우연히 같은 경우)은 2단계 리네임으로 안전하게 처리되므로, 여기서는
    배치에 속하지 않은 외부 파일과의 충돌만 막는다.
    """
    sources = {old for old, _new in pairs}
    for _old, new in pairs:
        if new.exists() and new not in sources:
            raise ValueError(
                f"리네임 대상 이름이 이미 존재하는 파일과 겹칩니다: {new} "
                "(파일이 뒤섞일 수 있어 중단합니다)"
            )


def apply_rename_plan(
    rows: list[RenamePlanRow],
    log_dir: Path,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[RenameResult]:
    """리네임 계획을 실제로 적용한다 (제자리 rename, 배치 전체를 2단계로 안전하게 수행).

    행 단위로 임시 이름을 거치면, 예를 들어 A의 목표 이름이 아직 처리 전인 B의
    원래 이름과 같은 "이름 스왑" 상황에서 A를 처리하는 순간 B를 덮어써버린다.
    그래서 1단계에서 이번 배치의 모든 파일을 임시 이름으로 옮겨 최종 이름 자리를
    전부 비운 뒤에, 2단계에서 각 파일을 최종 이름으로 옮긴다. 처리 내역은
    log_dir/rename_log_<타임스탬프>.jsonl에 기록한다.
    """
    log_dir = Path(log_dir)
    row_pairs_list = [
        [(row.old_path, row.new_path)]
        + ([(row.raw_old_path, row.raw_new_path)] if row.raw_old_path is not None else [])
        for row in rows
    ]
    flat_pairs = [pair for row_pairs in row_pairs_list for pair in row_pairs]
    _check_collisions(flat_pairs)

    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"rename_log_{datetime.datetime.now():%Y%m%d_%H%M%S}.jsonl"

    # 1단계: 전부 임시 이름으로 옮긴다. 도중 실패하면 지금까지 옮긴 것만 원상
    # 복구하고 중단한다 (최종 이름은 아직 아무것도 건드리지 않은 상태).
    temp_for: dict[tuple[Path, Path], Path] = {}
    renamed_so_far: list[tuple[Path, Path]] = []  # (temp, original)
    try:
        for old, new in flat_pairs:
            temp = old.with_name(f".__pixthrough_tmp_{uuid.uuid4().hex}{old.suffix}")
            old.rename(temp)
            temp_for[(old, new)] = temp
            renamed_so_far.append((temp, old))
    except OSError as e:
        for temp, original in reversed(renamed_so_far):
            if temp.exists():
                temp.rename(original)
        raise RuntimeError(f"리네임 1단계에서 실패해 모두 원상 복구했습니다: {e}") from e

    # 2단계: 임시 이름 -> 최종 이름. 이 시점엔 배치 안 이름 겹침이 전부 해소된
    # 상태라 실패할 여지가 거의 없어(디스크 오류 등 예외적 상황 제외) 자동
    # 롤백은 하지 않는다.
    # ponytail: 2단계 실패 시 자동 롤백 없음 — rename_log로 수동 복구,
    # 필요해지면 phase1처럼 되돌리는 로직 추가.
    total = len(rows)
    results: list[RenameResult] = []
    with open(log_path, "w", encoding="utf-8") as log_file:
        for index, (row, row_pairs) in enumerate(zip(rows, row_pairs_list), start=1):
            for old, new in row_pairs:
                temp_for[(old, new)].rename(new)

            results.append(
                RenameResult(
                    old_path=row.old_path,
                    new_path=row.new_path,
                    raw_old_path=row.raw_old_path,
                    raw_new_path=row.raw_new_path,
                )
            )
            log_file.write(
                json.dumps(
                    {
                        "timestamp": datetime.datetime.now().isoformat(),
                        **_row_to_report_dict(row),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            if on_progress is not None:
                on_progress(index, total)

    log.info("시간순 정렬 적용: %d개 파일", len(results))
    return results


def _guard_merge_overlap(rows: list[RenamePlanRow], output_dir: Path) -> None:
    """output_dir가 입력 폴더 중 하나와 같거나 포함 관계면 중단한다 (file_ops의
    같은 이름 가드와 동일한 취지 — 원본과 목적지가 뒤섞이는 걸 막는다)."""
    resolved_output = output_dir.resolve()
    for row in rows:
        source_parent = row.old_path.resolve().parent
        overlaps = (
            resolved_output == source_parent
            or resolved_output in source_parent.parents
            or source_parent in resolved_output.parents
        )
        if overlaps:
            raise ValueError(
                "출력 폴더가 입력 폴더와 같거나 그 안에 포함되어 있습니다: "
                "병합 대상이 뒤섞일 수 있어 중단합니다."
            )


def _check_merge_collisions(rows: list[RenamePlanRow]) -> None:
    """출력 폴더에 이미 있는 파일과 새 이름이 겹치면 아무것도 옮기기 전에 중단한다."""
    for row in rows:
        for target in filter(None, [row.new_path, row.raw_new_path]):
            if target.exists():
                raise ValueError(
                    f"병합 대상 이름이 이미 존재하는 파일과 겹칩니다: {target} "
                    "(파일이 뒤섞일 수 있어 중단합니다)"
                )


def apply_merge_plan(
    rows: list[RenamePlanRow],
    output_dir: Path,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[RenameResult]:
    """병합 계획을 실제로 적용한다 — 여러 입력 폴더의 파일을 output_dir 하나로 옮긴다.

    제자리 리네임과 달리 목적지가 항상 새 폴더라 이름 스왑 걱정이 없으므로
    (배치 안 파일끼리 목표 이름이 겹칠 일이 없음) 임시 이름을 거치는 2단계가
    필요 없다. shutil.move를 써서 출력 폴더가 다른 디스크/볼륨에 있어도(예:
    외장하드 원본 -> 로컬) 안전하게 옮긴다. 처리 내역은
    output_dir/rename_log_<타임스탬프>.jsonl에 기록한다.
    """
    output_dir = Path(output_dir)
    _guard_merge_overlap(rows, output_dir)
    _check_merge_collisions(rows)

    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / f"rename_log_{datetime.datetime.now():%Y%m%d_%H%M%S}.jsonl"

    total = len(rows)
    results: list[RenameResult] = []
    with open(log_path, "w", encoding="utf-8") as log_file:
        for index, row in enumerate(rows, start=1):
            row.new_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(row.old_path), str(row.new_path))
            if row.raw_old_path is not None:
                row.raw_new_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(row.raw_old_path), str(row.raw_new_path))

            results.append(
                RenameResult(
                    old_path=row.old_path,
                    new_path=row.new_path,
                    raw_old_path=row.raw_old_path,
                    raw_new_path=row.raw_new_path,
                )
            )
            log_file.write(
                json.dumps(
                    {
                        "timestamp": datetime.datetime.now().isoformat(),
                        **_row_to_report_dict(row),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            if on_progress is not None:
                on_progress(index, total)

    log.info("시간순 정렬 적용: %d개 파일", len(results))
    return results
