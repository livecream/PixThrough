"""M11: 폴더 정리 — RAW/영상 분리, 짝 안 맞는 파일 찾기, 촬영 날짜별 정리, 빈 폴더 정리,
완전 동일 파일(중복) 정리.

베스트컷/C컷과 달리 품질 판단이 없는 순수 파일 배치 작업이라 Qt 의존 없이
독립적으로 테스트 가능한 함수로 둔다. 이동/삭제 대상은 지정한 폴더의 최상위
파일만이며(하위 폴더는 건드리지 않음 — 이미 정리된 RAW/영상 하위 폴더를 다시
뒤섞지 않기 위함), 빈 폴더 탐색·중복 파일 탐색처럼 읽기 전용으로 먼저 훑는
작업만 하위까지 본다.

CLAUDE.md 원칙(원본 보호)에 따라 기본적으로 이동만 하고, 삭제는 (1) 파일이
하나도 없다고 확인된 "완전히 빈 폴더", (2) 바이트 단위로 완전히 동일하다고
확인된 "중복 파일"(그룹당 1개는 반드시 보존) 두 경우로만 한정한다 — 두 경우
모두 CLAUDE.md에 명시적 예외로 기록돼 있다.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from scanner import RAW_EXTENSIONS, SUPPORTED_EXTENSIONS, extract_metadata
import app_log

log = app_log.get("organize")


@dataclass(frozen=True)
class MovePlanRow:
    old_path: Path
    new_path: Path


@dataclass(frozen=True)
class FolderSummary:
    total_files: int
    total_size_bytes: int
    photo_count: int
    raw_count: int
    video_count: int
    other_count: int


def _top_level_files(folder: Path) -> list[Path]:
    return sorted(p for p in Path(folder).iterdir() if p.is_file())


def write_operation_log(items: list, folder: Path, action: str) -> Path:
    """정리 작업 내역을 folder/organize_log_<타임스탬프>.jsonl에 기록한다
    (rename_by_time.py의 rename_log_*.jsonl과 같은 형식). items는
    MovePlanRow 목록이거나(이동) Path 목록(폴더 삭제)이다."""
    folder = Path(folder)
    log_path = folder / f"organize_log_{datetime.datetime.now():%Y%m%d_%H%M%S}.jsonl"
    with open(log_path, "w", encoding="utf-8") as f:
        for item in items:
            if isinstance(item, MovePlanRow):
                entry = {"action": action, "old_path": str(item.old_path), "new_path": str(item.new_path)}
            else:
                entry = {"action": action, "path": str(item)}
            f.write(
                json.dumps({"timestamp": datetime.datetime.now().isoformat(), **entry}, ensure_ascii=False)
                + "\n"
            )
    log.info("폴더 정리 %s: %d개 (기록 %s)", action, len(items), log_path)
    return log_path


def build_undo_plan(rows: list[MovePlanRow]) -> list[MovePlanRow]:
    """이동 계획의 old/new를 뒤바꿔 되돌리기 계획을 만든다."""
    return [MovePlanRow(old_path=row.new_path, new_path=row.old_path) for row in rows]


def summarize_folder(folder: Path, video_extensions: set[str]) -> FolderSummary:
    """폴더 전체(하위 포함)의 파일 수/용량/카테고리별 개수를 센다.

    읽기 전용 정보 표시라 다른 정리 함수와 달리 하위 폴더까지 재귀적으로 봐도
    안전하다."""
    folder = Path(folder)
    raw_exts_lower = {ext.lower() for ext in RAW_EXTENSIONS}
    video_exts_lower = {ext.lower() for ext in video_extensions}

    total_files = 0
    total_size = 0
    photo_count = 0
    raw_count = 0
    video_count = 0
    other_count = 0

    for path in folder.rglob("*"):
        if not path.is_file():
            continue
        total_files += 1
        total_size += path.stat().st_size
        suffix = path.suffix.lower()
        if suffix in SUPPORTED_EXTENSIONS:
            photo_count += 1
        elif suffix in raw_exts_lower:
            raw_count += 1
        elif suffix in video_exts_lower:
            video_count += 1
        else:
            other_count += 1

    return FolderSummary(
        total_files=total_files,
        total_size_bytes=total_size,
        photo_count=photo_count,
        raw_count=raw_count,
        video_count=video_count,
        other_count=other_count,
    )


def build_move_by_extension_plan(
    folder: Path, extensions: set[str], subfolder_name: str
) -> list[MovePlanRow]:
    """folder 최상위에서 확장자가 일치하는 파일을 folder/subfolder_name/으로 옮기는 계획을 만든다."""
    exts_lower = {ext.lower() for ext in extensions}
    dest_dir = Path(folder) / subfolder_name
    return [
        MovePlanRow(old_path=path, new_path=dest_dir / path.name)
        for path in _top_level_files(folder)
        if path.suffix.lower() in exts_lower
    ]


def apply_move_plan(
    rows: list[MovePlanRow],
    log_folder: Path | None = None,
    action: str = "move",
    on_progress: Callable[[int, int], None] | None = None,
) -> list[MovePlanRow]:
    """계획된 이동을 실제로 수행하고 실제로 옮긴 것만 돌려준다. 대상 폴더는 필요하면 만든다.

    옮길 자리에 이미 같은 이름의 파일이 있으면 덮어쓰지 않고 건너뛴다(shutil.move는
    덮어쓰므로 미리 확인 — 원본 보호). 옮기다 실패해도 그때까지 옮긴 것은 기록한 뒤
    예외를 다시 던진다. log_folder가 주어지면 write_operation_log로 기록을 남긴다."""
    moved: list[MovePlanRow] = []
    try:
        for index, row in enumerate(rows, start=1):
            if row.new_path.exists():
                log.warning("이미 있어 건너뜀: %s → %s", row.old_path, row.new_path)
            else:
                row.new_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(row.old_path), str(row.new_path))
                moved.append(row)
            if on_progress is not None:
                on_progress(index, len(rows))
    finally:
        if log_folder is not None and moved:
            write_operation_log(moved, log_folder, action)
    return moved


def find_raw_jpg_mismatches(folder: Path) -> list[str]:
    """최상위 폴더에서 사진-RAW 베이스 파일명이 서로 짝이 안 맞는 경우를 사람이
    읽을 문자열 목록으로 반환한다 (이동/삭제 없음, 확인용 리포트)."""
    files = _top_level_files(folder)
    raw_exts_lower = {ext.lower() for ext in RAW_EXTENSIONS}
    photo_by_stem = {p.stem: p for p in files if p.suffix.lower() in SUPPORTED_EXTENSIONS}
    raw_by_stem = {p.stem: p for p in files if p.suffix.lower() in raw_exts_lower}

    messages = [
        f"RAW 없음: {photo.name}"
        for stem, photo in sorted(photo_by_stem.items())
        if stem not in raw_by_stem
    ]
    messages += [
        f"짝 사진 없음: {raw.name}"
        for stem, raw in sorted(raw_by_stem.items())
        if stem not in photo_by_stem
    ]
    return messages


def build_capture_date_plan(
    folder: Path,
    video_extensions: set[str],
    on_progress: Callable[[int, int], None] | None = None,
) -> list[MovePlanRow]:
    """최상위 파일(사진+영상)을 촬영 날짜(YYYY-MM-DD) 하위 폴더로 옮기는 계획을 만든다.

    사진은 EXIF 촬영 시각, 영상은 파일 수정 시각을 쓴다 (rename_by_time.py와
    동일한 방식 — 영상엔 촬영 시각 EXIF가 없음)."""
    folder = Path(folder)
    video_exts_lower = {ext.lower() for ext in video_extensions}
    rows = []
    files = _top_level_files(folder)
    for index, path in enumerate(files, start=1):
        if on_progress is not None:
            on_progress(index, len(files))
        suffix = path.suffix.lower()
        if suffix in video_exts_lower:
            dt = datetime.datetime.fromtimestamp(path.stat().st_mtime)
        elif suffix in SUPPORTED_EXTENSIONS:
            dt = extract_metadata(path).datetime_original
        else:
            continue
        date_dir = folder / dt.strftime("%Y-%m-%d")
        rows.append(MovePlanRow(old_path=path, new_path=date_dir / path.name))
    return rows


def find_empty_folders(root: Path) -> list[Path]:
    """root 아래(자기 자신 제외) 파일이 하나도 없는 폴더를 깊은 순서로 전부 찾는다.

    깊은 폴더부터 정렬해 반환하므로, 반환 순서 그대로 삭제하면 부모보다 자식이
    먼저 지워져 부모도 빈 폴더가 된 뒤 안전하게 지워진다."""
    root = Path(root)
    subdirs = [p for p in root.rglob("*") if p.is_dir()]
    subdirs.sort(key=lambda p: len(p.parts), reverse=True)
    return [d for d in subdirs if not any(f.is_file() for f in d.rglob("*"))]


def remove_empty_folders(folders: list[Path], log_folder: Path | None = None) -> None:
    """빈 폴더 목록을 실제로 삭제한다.

    log_folder가 주어지면 삭제 후 write_operation_log로 기록을 남긴다."""
    for folder in folders:
        folder.rmdir()
    if log_folder is not None and folders:
        write_operation_log(folders, log_folder, "remove_empty_folders")


@dataclass(frozen=True)
class DuplicateGroup:
    keep: Path
    duplicates: list[Path]  # 삭제 후보 (keep 제외 나머지, 전부 keep과 바이트 단위로 동일)


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_duplicate_files(
    root: Path, on_progress: Callable[[int, int], None] | None = None
) -> list[DuplicateGroup]:
    """root 아래(하위 포함) 바이트 단위로 완전히 동일한 파일을 찾는다.

    같은 크기끼리 먼저 묶은 뒤 해시로 확인해 불필요한 해시 계산을 줄인다.
    각 그룹에서 경로 문자열이 가장 앞선 파일을 "보존"으로 삼고 나머지를
    삭제 후보로 분류한다(늘 같은 결과가 나오도록 결정적 규칙). 읽기 전용
    탐색이라 하위 폴더까지 전부 본다."""
    root = Path(root)
    by_size: dict[int, list[Path]] = {}
    for path in root.rglob("*"):
        if path.is_file():
            by_size.setdefault(path.stat().st_size, []).append(path)

    groups: list[DuplicateGroup] = []
    to_hash = sum(len(c) for c in by_size.values() if len(c) >= 2)
    hashed = 0
    for candidates in by_size.values():
        if len(candidates) < 2:
            continue
        by_hash: dict[str, list[Path]] = {}
        for path in candidates:
            by_hash.setdefault(_file_hash(path), []).append(path)
            hashed += 1
            if on_progress is not None:
                on_progress(hashed, to_hash)
        for paths in by_hash.values():
            if len(paths) < 2:
                continue
            paths_sorted = sorted(paths, key=str)
            groups.append(DuplicateGroup(keep=paths_sorted[0], duplicates=paths_sorted[1:]))
    return groups


def delete_duplicate_files(groups: list[DuplicateGroup], log_folder: Path | None = None) -> int:
    """중복 그룹의 삭제 후보 파일들을 실제로 삭제하고 삭제한 개수를 반환한다.

    CLAUDE.md 원칙(원본 삭제 금지)의 명시적 예외 — 그룹마다 최소 1개(keep)는
    항상 남으므로 사진 자체가 사라지는 일은 없다. 삭제는 되돌릴 수 없다."""
    deleted_paths = [path for group in groups for path in group.duplicates]
    for path in deleted_paths:
        path.unlink()
    if log_folder is not None and deleted_paths:
        write_operation_log(deleted_paths, log_folder, "delete_duplicate")
    return len(deleted_paths)
