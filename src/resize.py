"""사진 축소: 폴더 안 사진을 긴 변(장축) 기준 지정한 픽셀로 줄여 JPG로 새 폴더에 저장한다.

사용자 결정(PROJECT_SPEC 8절): 원본은 그대로 두고 고른 출력 폴더에 같은 파일 이름으로
만든다. JPG·PNG·HEIC 같은 일반 사진만 대상이고 RAW는 건너뛴다. 항상 JPG로 저장하며
긴 변 픽셀과 화질은 사용자가 정한다. 촬영 정보(EXIF, 회전 정보 포함)와 색 프로필은
그대로 옮기고, 파일 수정 시각도 원본과 같게 둔다(시간순 정렬 등에서 그대로 쓰도록).

- 이미 긴 변이 지정한 픽셀 이하인 사진은 키우지 않는다: JPG는 원본을 그대로 복사하고,
  JPG가 아니면 같은 크기로 JPG로만 바꾼다.
- 하위 폴더까지 포함하면 출력 폴더에 같은 하위 폴더 구조를 만든다.
- 출력 폴더에 같은 이름이 있으면 덮어쓰지 않고 " (1)"을 붙인다(a.png와 a.jpg가 둘 다
  a.jpg가 되는 경우도 같다).
- 먼저 계획(build_resize_plan, 파일 머리만 읽어 빠름)을 보여주고, 적용
  (apply_resize_plan)할 때만 파일을 만든다. 처리 내역은 출력 폴더의
  resize_log_<타임스탬프>.jsonl에 남는다.
"""

from __future__ import annotations

import datetime
import json
import os
import shutil
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from PIL import Image

from raw_preview import RAW_EXTENSIONS
from scanner import SUPPORTED_EXTENSIONS  # HEIC를 열 수 있게 pillow_heif도 등록된다

LONG_EDGE_MIN = 100
LONG_EDGE_MAX = 20_000
JPEG_EXTENSIONS = {".jpg", ".jpeg"}

ACTION_RESIZE = "resize"  # 줄여서 JPG로 저장
ACTION_CONVERT = "convert"  # 이미 작지만 JPG가 아니라 같은 크기로 JPG로만 바꿈
ACTION_COPY = "copy"  # 이미 작은 JPG라 그대로 복사
ACTION_ERROR = "error"  # 열 수 없는 파일(손상 등) — 건너뜀

ACTION_LABELS = {
    ACTION_RESIZE: "축소",
    ACTION_CONVERT: "JPG로 변환 (이미 작음)",
    ACTION_COPY: "그대로 복사 (이미 작음)",
    ACTION_ERROR: "건너뜀 (열 수 없음)",
}

# EXIF 회전 값 중 가로·세로가 바뀌는 것(5~8). 화면에 보이는 크기를 알려줄 때만 쓴다.
_SWAPPED_ORIENTATIONS = {5, 6, 7, 8}
_ORIENTATION_TAG = 0x0112


@dataclass(frozen=True)
class ResizePlanRow:
    source: Path
    destination: Path
    # 화면에 보이는 방향(회전 정보 반영)의 가로×세로. 열 수 없으면 None
    original_size: tuple[int, int] | None
    new_size: tuple[int, int] | None
    action: str
    error: str | None = None


@dataclass(frozen=True)
class ResizePlan:
    rows: list[ResizePlanRow]
    skipped_raw: int  # RAW라서 건너뛴 파일 수


@dataclass(frozen=True)
class ResizeResult:
    source: Path
    destination: Path | None
    action: str
    error: str | None = None


def target_size(width: int, height: int, long_edge: int) -> tuple[int, int]:
    """긴 변이 long_edge가 되게 비율대로 줄인 크기. 이미 작으면 그대로(키우지 않음)."""
    longest = max(width, height)
    if longest <= long_edge:
        return width, height
    scale = long_edge / longest
    if width >= height:
        return long_edge, max(1, round(height * scale))
    return max(1, round(width * scale)), long_edge


def _guard_against_overlap(input_dir: Path, output_dir: Path) -> None:
    resolved_input = input_dir.resolve()
    resolved_output = output_dir.resolve()
    if (
        resolved_output == resolved_input
        or resolved_input in resolved_output.parents
        or resolved_output in resolved_input.parents
    ):
        raise ValueError(
            "출력 폴더가 입력 폴더와 같거나 서로 포함 관계입니다: 원본 옆에 축소본이 섞일 수 "
            "있어 중단합니다. 입력 폴더와 겹치지 않는 출력 폴더를 고르세요."
        )


def _list_files(input_dir: Path, recursive: bool) -> list[Path]:
    pattern = input_dir.rglob("*") if recursive else input_dir.iterdir()
    return sorted(p for p in pattern if p.is_file() and not p.name.startswith("."))


def _displayed_size(image: Image.Image) -> tuple[int, int]:
    width, height = image.size
    try:
        orientation = image.getexif().get(_ORIENTATION_TAG)
    except Exception:  # noqa: BLE001 - 깨진 EXIF는 회전 정보 없음으로 본다
        orientation = None
    return (height, width) if orientation in _SWAPPED_ORIENTATIONS else (width, height)


def _unique(candidate: Path, taken: set[str]) -> Path:
    """이미 있거나(디스크) 이번 계획에서 쓰기로 한 이름(taken, 소문자)이면 " (n)"을 붙인다."""
    stem, suffix = candidate.stem, candidate.suffix
    path = candidate
    n = 1
    while str(path).lower() in taken or path.exists():
        path = candidate.with_name(f"{stem} ({n}){suffix}")
        n += 1
    taken.add(str(path).lower())
    return path


def build_resize_plan(
    input_dir: Path,
    output_dir: Path,
    long_edge: int,
    recursive: bool = False,
    on_progress: Callable[[int, int], None] | None = None,
) -> ResizePlan:
    """무엇을 어떻게 저장할지 계획한다(파일은 만들지 않음). 사진은 머리만 읽어 크기를 안다."""
    input_dir, output_dir = Path(input_dir), Path(output_dir)
    if not LONG_EDGE_MIN <= long_edge <= LONG_EDGE_MAX:
        raise ValueError(f"긴 변 픽셀은 {LONG_EDGE_MIN}~{LONG_EDGE_MAX} 사이여야 합니다.")
    _guard_against_overlap(input_dir, output_dir)

    raw_exts = {ext.lower() for ext in RAW_EXTENSIONS}
    files = _list_files(input_dir, recursive)
    photos = [p for p in files if p.suffix.lower() in SUPPORTED_EXTENSIONS]
    skipped_raw = sum(1 for p in files if p.suffix.lower() in raw_exts)

    rows: list[ResizePlanRow] = []
    taken: set[str] = set()
    for index, source in enumerate(photos, start=1):
        relative = source.relative_to(input_dir)
        is_jpeg = source.suffix.lower() in JPEG_EXTENSIONS
        name = relative.name if is_jpeg else relative.stem + ".jpg"
        destination = output_dir / relative.parent / name
        try:
            with Image.open(source) as image:
                shown = _displayed_size(image)
        except Exception as e:  # noqa: BLE001 - 손상 파일 등은 건너뛰고 알린다
            rows.append(ResizePlanRow(source, destination, None, None, ACTION_ERROR, str(e)))
        else:
            row = ResizePlanRow(source, _unique(destination, taken), shown, None, ACTION_RESIZE)
            rows.append(_decide(row, long_edge))
        if on_progress is not None:
            on_progress(index, len(photos))
    return ResizePlan(rows=rows, skipped_raw=skipped_raw)


def _decide(row: ResizePlanRow, long_edge: int) -> ResizePlanRow:
    if row.original_size is None:
        return row
    new_size = target_size(*row.original_size, long_edge)
    if new_size != row.original_size:
        action = ACTION_RESIZE
    else:
        action = ACTION_COPY if row.source.suffix.lower() in JPEG_EXTENSIONS else ACTION_CONVERT
    return replace(row, new_size=new_size, action=action)


def replan(rows: list[ResizePlanRow], long_edge: int) -> list[ResizePlanRow]:
    """긴 변 픽셀만 바꿨을 때 사진을 다시 읽지 않고 새 크기·처리를 다시 계산한다."""
    if not LONG_EDGE_MIN <= long_edge <= LONG_EDGE_MAX:
        raise ValueError(f"긴 변 픽셀은 {LONG_EDGE_MIN}~{LONG_EDGE_MAX} 사이여야 합니다.")
    return [_decide(row, long_edge) for row in rows]


def _to_jpeg_mode(image: Image.Image) -> Image.Image:
    if image.mode in ("RGB", "L", "CMYK"):
        return image
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return image.convert("RGB")


def _save_resized(source: Path, destination: Path, long_edge: int, quality: int) -> None:
    with Image.open(source) as image:
        exif = image.info.get("exif")
        icc_profile = image.info.get("icc_profile")
        # 픽셀은 회전하지 않고 회전 정보(EXIF)를 그대로 옮긴다 — 보는 프로그램이 원본과
        # 똑같이 돌려 보여준다. 긴 변은 회전과 무관하다.
        width, height = target_size(*image.size, long_edge)
        if image.format == "JPEG":
            # 큰 JPEG는 목표의 2배 정도까지만 풀어(DCT 축소) 빠르게, 나머지는 고품질 축소
            image.draft(image.mode, (width * 2, height * 2))
        image = _to_jpeg_mode(image)
        if image.size != (width, height):
            image = image.resize((width, height), Image.Resampling.LANCZOS)
        options = {"quality": quality}
        if exif:
            options["exif"] = exif
        if icc_profile:
            options["icc_profile"] = icc_profile
        image.save(destination, "JPEG", **options)


def apply_resize_plan(
    rows: list[ResizePlanRow],
    output_dir: Path,
    long_edge: int,
    quality: int,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[ResizeResult]:
    """계획대로 축소본을 만든다. 원본은 읽기만 하고, 출력 폴더의 기존 파일은 덮어쓰지 않는다.
    한 장이 실패해도 나머지는 계속하고, 실패는 결과와 로그에 남긴다."""
    if not 1 <= quality <= 100:
        raise ValueError("JPG 화질은 1~100 사이여야 합니다.")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / f"resize_log_{datetime.datetime.now():%Y%m%d_%H%M%S}.jsonl"
    todo = [row for row in rows if row.action != ACTION_ERROR]
    results: list[ResizeResult] = []
    taken: set[str] = set()
    with open(log_path, "w", encoding="utf-8") as log_file:
        for index, row in enumerate(todo, start=1):
            entry = {
                "timestamp": datetime.datetime.now().isoformat(),
                "action": row.action,
                "source": str(row.source),
                "original_size": row.original_size,
                "new_size": row.new_size,
                "long_edge": long_edge,
                "quality": quality,
            }
            # 미리보기 뒤에 같은 이름이 생겼을 수 있어 다시 확인(덮어쓰지 않음)
            destination = _unique(row.destination, taken)
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                if row.action == ACTION_COPY:
                    shutil.copy2(row.source, destination)
                else:
                    _save_resized(row.source, destination, long_edge, quality)
                    stat = row.source.stat()
                    os.utime(destination, (stat.st_atime, stat.st_mtime))
            except Exception as e:  # noqa: BLE001 - 한 장 실패로 전체를 멈추지 않는다
                try:
                    destination.unlink()  # 반쯤 쓴 축소본(원본 아님)만 지운다
                except OSError:
                    pass
                entry["error"] = str(e)
                results.append(ResizeResult(row.source, None, row.action, str(e)))
            else:
                entry["destination"] = str(destination)
                results.append(ResizeResult(row.source, destination, row.action))
            log_file.write(json.dumps(entry, ensure_ascii=False) + "\n")
            if on_progress is not None:
                on_progress(index, len(todo))
    return results
