"""바디별 시계 맞추기: 카메라마다 시계가 어긋난 만큼(시차)을 더해 촬영 시각을 맞춘다.

바디 두 대의 시계가 몇 초~몇 분 어긋나 있으면 시간순 정렬(00001~) 순서가 섞이고, 장면
묶기에서 같은 순간이 다른 장면으로 갈리거나 다른 장면이 붙는다. 사용자 결정(PROJECT_SPEC 8절):
- 바디는 카메라 기종+시리얼(EXIF BodySerialNumber)로 구분한다. 시리얼이 없으면 기종까지만.
  카메라 정보가 없는 파일(영상 등)은 같은 폴더에 가장 많은 카메라를 따른다(없으면 보정 안 함).
- 시차는 사용자가 직접 넣거나, 같은 순간을 찍은 두 사진(기준 바디 하나, 맞출 바디 하나)으로
  계산한다(offset_from_pair).
- 원본 파일의 촬영 시각은 고치지 않는다. 시간순 정렬·장면 묶기 계산에만 쓴다.
"""

from __future__ import annotations

import datetime
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path

from scanner import PhotoMetadata

MAX_OFFSET_SECONDS = 24 * 3600 - 1


def camera_key(meta: PhotoMetadata | None) -> str | None:
    """바디를 구분하는 이름(예: "Canon EOS R5 #012345678"). 카메라 정보가 없으면 None."""
    if meta is None:
        return None
    make = (meta.camera_make or "").strip()
    model = (meta.camera_model or "").strip()
    if not make and not model:
        return None
    # "Canon" + "Canon EOS R5"처럼 기종에 제조사가 이미 들어 있으면 한 번만
    name = model if make and model.lower().startswith(make.lower().split()[0]) else f"{make} {model}".strip()
    return f"{name} #{meta.camera_serial}" if meta.camera_serial else name


def camera_keys(items: list[tuple[Path, PhotoMetadata | None]]) -> dict[Path, str | None]:
    """파일마다 바디 이름. 카메라 정보가 없는 파일은 같은 폴더에서 가장 많은 바디를 따른다."""
    keys = {path: camera_key(meta) for path, meta in items}
    by_folder: dict[Path, Counter] = {}
    for path, key in keys.items():
        if key is not None:
            by_folder.setdefault(path.parent, Counter())[key] += 1
    for path, key in keys.items():
        if key is None and path.parent in by_folder:
            keys[path] = by_folder[path.parent].most_common(1)[0][0]
    return keys


def shifted(time: datetime.datetime, key: str | None, offsets: dict[str, float]) -> datetime.datetime:
    seconds = offsets.get(key, 0) if key is not None else 0
    return time + datetime.timedelta(seconds=seconds) if seconds else time


def shift_photos(photos: list[PhotoMetadata], offsets: dict[str, float]) -> list[PhotoMetadata]:
    """시차를 더한 사본을 촬영 시각순으로(같으면 경로순) 돌려준다. 시차가 없으면 그대로."""
    if not any(offsets.values()):
        return photos
    keys = camera_keys([(p.path, p) for p in photos])
    moved = [replace(p, datetime_original=shifted(p.datetime_original, keys[p.path], offsets)) for p in photos]
    return sorted(moved, key=lambda p: (p.datetime_original, str(p.path)))


@dataclass(frozen=True)
class CameraInfo:
    key: str
    count: int
    first: datetime.datetime
    last: datetime.datetime
    example: Path


def summarize(photos: list[PhotoMetadata]) -> tuple[list[CameraInfo], int]:
    """사진에 나온 바디들(사진 많은 순)과, 바디를 알 수 없는 파일 수."""
    keys = camera_keys([(p.path, p) for p in photos])
    groups: dict[str, list[PhotoMetadata]] = {}
    unknown = 0
    for photo in photos:
        key = keys[photo.path]
        if key is None:
            unknown += 1
        else:
            groups.setdefault(key, []).append(photo)
    cameras = [
        CameraInfo(
            key,
            len(members),
            min(p.datetime_original for p in members),
            max(p.datetime_original for p in members),
            members[0].path,
        )
        for key, members in groups.items()
    ]
    cameras.sort(key=lambda c: (-c.count, c.key))
    return cameras, unknown


def offset_from_pair(
    reference: PhotoMetadata, other: PhotoMetadata, offsets: dict[str, float]
) -> tuple[str, float]:
    """같은 순간을 찍은 두 사진으로, other 바디에 넣을 시차를 구한다(reference 바디의 시차는
    그대로 두고 맞춤). 돌려준 값: (other 바디 이름, 시차 초)."""
    reference_key, other_key = camera_key(reference), camera_key(other)
    if reference_key is None or other_key is None:
        raise ValueError("카메라 정보가 없는 사진은 고를 수 없습니다.")
    if reference_key == other_key:
        raise ValueError("서로 다른 바디로 찍은 사진 두 장을 골라 주세요.")
    target = shifted(reference.datetime_original, reference_key, offsets)
    seconds = (target - other.datetime_original).total_seconds()
    if abs(seconds) > MAX_OFFSET_SECONDS:
        raise ValueError("두 사진의 시각이 하루 넘게 차이 납니다. 날짜 설정을 먼저 확인해 주세요.")
    return other_key, seconds


def format_offset(seconds: float) -> str:
    """+1:23, -0:05, +2:03:04처럼(0이면 "0:00")."""
    total = int(round(seconds))
    sign = "-" if total < 0 else "+" if total > 0 else ""
    total = abs(total)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    body = f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"
    return sign + body


def parse_offset(text: str) -> float:
    """format_offset의 반대("+1:23", "-0:05", "+2:03:04", "90"). 잘못되면 ValueError."""
    text = text.strip().replace(" ", "")
    if not text:
        return 0.0
    sign = -1 if text.startswith("-") else 1
    parts = text.lstrip("+-").split(":")
    if not 1 <= len(parts) <= 3 or not all(part.isdigit() for part in parts):
        raise ValueError(f"시차 형식이 아닙니다: {text}")
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + int(part)
    if seconds > MAX_OFFSET_SECONDS:
        raise ValueError("시차는 하루보다 작아야 합니다.")
    return float(sign * seconds)
