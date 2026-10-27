"""M1: 폴더 스캔 + EXIF 메타데이터 추출.

지정한 폴더(옵션에 따라 하위 폴더 포함)를 스캔해서 지원 포맷 사진 파일 목록을 찾고,
각 파일의 EXIF 메타데이터(촬영 시각, 카메라 설정, 방향)를 추출한다.

RAW 파일(CR2/NEF/ARW 등)은 기본적으로 스캔·스코어링 대상에 포함하지 않는다
(PROJECT_SPEC.md 8절) — 같은 이름의 JPG로 점수를 매기고 RAW는 복사할 때 짝으로 따라간다.
예외로 include_unpaired_raw=True(베스트컷·C컷)면 짝 JPG가 없는 RAW(RAW만 찍은 폴더)를
RAW 안의 미리보기 JPEG로 대신 읽어 포함한다 — raw_preview.py.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import exifread
from PIL import Image
import pillow_heif

from raw_preview import RAW_EXTENSIONS, is_raw, raw_capture_time
from raw_preview import _read_preview as _read_raw_preview

pillow_heif.register_heif_opener()
logging.getLogger("exifread").setLevel(logging.ERROR)  # PNG 등 EXIF 없는 파일의 경고 억제

# 1차 스캔 대상 포맷 (PROJECT_SPEC.md 3절)
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".heif"}

# RAW_EXTENSIONS(raw_preview에서 가져옴): 기본 스캔 대상에는 포함하지 않지만, 베스트 컷
# 선정 후 파일 짝짓기(M6)와 짝 없는 RAW 포함(include_unpaired_raw)에 쓴다.


@dataclass
class PhotoMetadata:
    path: Path
    datetime_original: datetime.datetime
    exif_source: str  # "exif" | "mtime" (EXIF 촬영 시각이 없어 파일 수정 시각으로 대체한 경우)
    orientation: int | None = None
    camera_make: str | None = None
    camera_model: str | None = None
    camera_serial: str | None = None  # 같은 기종 바디 두 대를 구분(바디별 시계 맞추기)
    aperture: str | None = None
    iso: str | None = None
    shutter_speed: str | None = None
    width: int | None = None
    height: int | None = None
    error: str | None = None


def _photo_stems(folder: Path, cache: dict[Path, set[str]]) -> set[str]:
    """folder 안 사진(JPG 등)의 베이스 파일명(소문자) 모음. 폴더마다 한 번만 읽는다."""
    if folder not in cache:
        try:
            cache[folder] = {
                p.stem.lower()
                for p in folder.iterdir()
                if p.suffix.lower() in SUPPORTED_EXTENSIONS and p.is_file()
            }
        except OSError:
            cache[folder] = set()
    return cache[folder]


def _has_photo_sibling(raw_path: Path, cache: dict[Path, set[str]]) -> bool:
    """RAW와 베이스 파일명이 같은 사진(JPG 등)이 있는지. 같은 폴더, 그리고 RAW가 RAW/
    하위 폴더에 있으면(베스트컷 복사본 레이아웃) 바로 위 폴더도 본다 — find_matching_raw와
    같은 짝짓기 규칙."""
    stem = raw_path.stem.lower()
    folders = [raw_path.parent]
    if raw_path.parent.name.lower() == "raw":
        folders.append(raw_path.parent.parent)
    return any(stem in _photo_stems(folder, cache) for folder in folders)


def scan_folder(
    input_dir: Path, recursive: bool = False, include_unpaired_raw: bool = False
) -> list[Path]:
    """지원 확장자의 사진 파일 경로 목록을 반환한다 (RAW 제외).

    include_unpaired_raw=True면 같은 이름의 사진이 없는 RAW도 포함한다(RAW만 찍은 폴더)."""
    input_dir = Path(input_dir)
    if not input_dir.is_dir():
        raise NotADirectoryError(f"입력 폴더를 찾을 수 없습니다: {input_dir}")

    pattern_iter = input_dir.rglob("*") if recursive else input_dir.glob("*")
    files = []
    photo_stems: dict[Path, set[str]] = {}
    for p in pattern_iter:
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext in SUPPORTED_EXTENSIONS:
            files.append(p)
        elif include_unpaired_raw and ext in RAW_EXTENSIONS and not _has_photo_sibling(p, photo_stems):
            files.append(p)
    return sorted(files)


def _image_size(path: Path) -> tuple[int, int]:
    """사진의 (가로, 세로). RAW는 안에 든 미리보기 크기(세로 사진이면 바꿔서)."""
    if is_raw(path):
        img, flip = _read_raw_preview(path)
        width, height = img.size
        return (height, width) if flip in (5, 6) else (width, height)
    with Image.open(path) as img:
        return img.size


def _parse_exif_datetime(value: str) -> datetime.datetime | None:
    # EXIF 표준 포맷: "YYYY:MM:DD HH:MM:SS"
    try:
        return datetime.datetime.strptime(str(value), "%Y:%m:%d %H:%M:%S")
    except (ValueError, TypeError):
        return None


def extract_metadata(path: Path) -> PhotoMetadata:
    """단일 파일의 EXIF 메타데이터를 추출한다.

    DateTimeOriginal이 없는 파일(스크린샷 등)은 파일의 수정 시각(mtime)을 대신 쓰고
    exif_source="mtime"으로 표시해 후속 단계(리포트)에서 구분할 수 있게 한다.
    """
    path = Path(path)
    width = height = None
    try:
        width, height = _image_size(path)
    except Exception as e:  # 손상된 파일, 미리보기가 없는 RAW 등
        return PhotoMetadata(
            path=path,
            datetime_original=datetime.datetime.fromtimestamp(path.stat().st_mtime),
            exif_source="mtime",
            error=f"이미지를 열 수 없음: {e}",
        )

    dt_original = None
    orientation = camera_make = camera_model = camera_serial = None
    aperture = iso = shutter_speed = None

    try:
        with open(path, "rb") as f:
            tags = exifread.process_file(f, details=False)

        dt_raw = tags.get("EXIF DateTimeOriginal") or tags.get("Image DateTime")
        if dt_raw is not None:
            dt_original = _parse_exif_datetime(str(dt_raw))

        if "Image Orientation" in tags:
            orientation = tags["Image Orientation"].values[0] if tags["Image Orientation"].values else None
        if "Image Make" in tags:
            camera_make = str(tags["Image Make"]).strip()
        if "Image Model" in tags:
            camera_model = str(tags["Image Model"]).strip()
        serial = tags.get("EXIF BodySerialNumber")
        if serial is not None and str(serial).strip():
            camera_serial = str(serial).strip()
        if "EXIF FNumber" in tags:
            aperture = str(tags["EXIF FNumber"])
        if "EXIF ISOSpeedRatings" in tags:
            iso = str(tags["EXIF ISOSpeedRatings"])
        if "EXIF ExposureTime" in tags:
            shutter_speed = str(tags["EXIF ExposureTime"])
    except Exception:
        pass  # EXIF 파싱 실패 시 mtime 폴백으로 넘어감

    if dt_original is None and is_raw(path):
        dt_original = raw_capture_time(path)  # exifread가 못 읽는 CR3 등
    if dt_original is not None:
        exif_source = "exif"
    else:
        dt_original = datetime.datetime.fromtimestamp(path.stat().st_mtime)
        exif_source = "mtime"

    return PhotoMetadata(
        path=path,
        datetime_original=dt_original,
        exif_source=exif_source,
        orientation=orientation,
        camera_make=camera_make,
        camera_model=camera_model,
        camera_serial=camera_serial,
        aperture=aperture,
        iso=iso,
        shutter_speed=shutter_speed,
        width=width,
        height=height,
    )


def _stat_key(path: Path) -> tuple[float, int]:
    stat = path.stat()
    return stat.st_mtime, stat.st_size


def _extract_metadata_cached(
    path: Path, cache: dict[Path, tuple[float, int, PhotoMetadata]] | None
) -> PhotoMetadata:
    """cache가 주어지면 파일의 (수정 시각, 크기)가 이전과 같을 때 다시 읽지 않고
    캐시된 메타데이터를 재사용한다. 세션(앱 실행 중) 동안만 유지되는 인메모리
    캐시로, 호출자가 dict를 만들어 여러 번의 스캔에 걸쳐 재사용하는 방식이다."""
    if cache is None:
        return extract_metadata(path)
    key = _stat_key(path)
    cached = cache.get(path)
    if cached is not None and cached[:2] == key:
        return cached[2]
    meta = extract_metadata(path)
    cache[path] = (*key, meta)
    return meta


def scan_and_extract(
    input_dir: Path,
    recursive: bool = False,
    on_progress: Callable[[int, int, Path], None] | None = None,
    cache: dict[Path, tuple[float, int, PhotoMetadata]] | None = None,
    include_unpaired_raw: bool = False,
) -> list[PhotoMetadata]:
    """폴더를 스캔하고 각 파일의 메타데이터를 추출해 촬영 시각순으로 정렬해 반환한다.

    on_progress가 주어지면 파일 1개를 처리할 때마다 (완료한 수, 전체 수, 방금
    처리한 파일 경로)를 알려준다."""
    files = scan_folder(input_dir, recursive=recursive, include_unpaired_raw=include_unpaired_raw)
    total = len(files)
    results = []
    for i, f in enumerate(files, start=1):
        results.append(_extract_metadata_cached(f, cache))
        if on_progress is not None:
            on_progress(i, total, f)
    results.sort(key=lambda m: m.datetime_original)
    return results


def scan_and_extract_many(
    input_dirs: list[Path],
    recursive: bool = False,
    on_progress: Callable[[int, int, Path], None] | None = None,
    cache: dict[Path, tuple[float, int, PhotoMetadata]] | None = None,
    include_unpaired_raw: bool = False,
) -> list[PhotoMetadata]:
    """여러 폴더(예: 바디별 폴더)를 스캔해 촬영 시각 기준으로 합쳐 정렬한다."""
    files = [
        f
        for input_dir in input_dirs
        for f in scan_folder(input_dir, recursive=recursive, include_unpaired_raw=include_unpaired_raw)
    ]
    total = len(files)
    results = []
    for i, f in enumerate(files, start=1):
        results.append(_extract_metadata_cached(f, cache))
        if on_progress is not None:
            on_progress(i, total, f)
    results.sort(key=lambda m: m.datetime_original)
    return results
