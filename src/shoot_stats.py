"""촬영 통계: 폴더(또는 베스트컷 미리보기) 사진의 바디·렌즈별 장 수, 시간대별 촬영량, ISO·셔터속도·
조리개·초점거리 분포, (C컷 판정이 있으면) 바디별 C컷 비율. 어느 바디나 시간대에 흔들린 컷이 많았는지
돌아보는 촬영 피드백용이다. 파일은 읽기만 한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from clock_sync import camera_key
from scanner import PhotoMetadata


@dataclass(frozen=True)
class Bucket:
    label: str
    count: int
    extra: str = ""  # 덧붙일 글(C컷 비율 등)


@dataclass
class Section:
    title: str
    buckets: list[Bucket]
    note: str = ""


@dataclass
class Stats:
    total: int
    first: object | None
    last: object | None
    sections: list[Section] = field(default_factory=list)
    unknown_time: int = 0


def collect(folder: Path, recursive: bool = True, on_progress=None) -> list[PhotoMetadata]:
    """폴더(하위 포함)의 사진 촬영 정보(JPG+RAW 짝은 하나, RAW만이면 RAW)."""
    from scanner import scan_and_extract

    progress = (lambda done, total, _path: on_progress(done, total)) if on_progress else None
    return scan_and_extract(Path(folder), recursive=recursive, include_unpaired_raw=True, on_progress=progress)


def _number(text: str | None) -> float | None:
    """EXIF 글자("1/250", "28/10", "[100, 0]", "0.5")를 숫자로."""
    if text is None:
        return None
    text = str(text).strip().strip("[]").split(",")[0].strip()
    if not text:
        return None
    try:
        return float(Fraction(text))
    except (ValueError, ZeroDivisionError):
        return None


def _binned(values: list[float], edges: list[tuple[float, str]], last_label: str) -> list[Bucket]:
    """edges: (이하 상한, 이름) 오름차순. 상한을 넘으면 last_label."""
    counts = {label: 0 for _limit, label in edges}
    counts[last_label] = 0
    for value in values:
        for limit, label in edges:
            if value <= limit:
                counts[label] += 1
                break
        else:
            counts[last_label] += 1
    return [Bucket(label, count) for label, count in counts.items() if count]


def _counted(keys: list[str]) -> list[Bucket]:
    counts: dict[str, int] = {}
    for key in keys:
        counts[key] = counts.get(key, 0) + 1
    return [Bucket(k, v) for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def compute(photos: list[PhotoMetadata], flagged: set[Path] | None = None, kept: set[Path] | None = None) -> Stats:
    """flagged(C컷)·kept(남기는 컷)가 있으면 바디별 비율도 적는다(베스트컷 미리보기에서 열 때)."""
    photos = [p for p in photos if p.error is None]
    times = [p.datetime_original for p in photos if p.exif_source == "exif"]
    stats = Stats(len(photos), min(times) if times else None, max(times) if times else None,
                  unknown_time=len(photos) - len(times))

    bodies: dict[str, list[PhotoMetadata]] = {}
    for meta in photos:
        bodies.setdefault(camera_key(meta) or "(카메라 정보 없음)", []).append(meta)
    body_buckets = []
    for name, items in sorted(bodies.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        extra = []
        if kept is not None:
            extra.append(f"남김 {sum(1 for m in items if m.path in kept)}")
        if flagged is not None:
            bad = sum(1 for m in items if m.path in flagged)
            extra.append(f"C컷 {bad} ({bad * 100 // max(1, len(items))}%)")
        body_buckets.append(Bucket(name, len(items), " · ".join(extra)))
    note = "" if flagged is not None else "C컷 비율은 베스트컷·C컷 화면에서 C컷 기준을 정하고 통계를 열면 보입니다."
    stats.sections.append(Section("바디", body_buckets, note))

    lenses = [m.lens for m in photos if m.lens]
    if lenses:
        stats.sections.append(Section("렌즈", _counted(lenses), f"렌즈 정보 없음 {len(photos) - len(lenses)}장" if len(lenses) < len(photos) else ""))

    if times:
        hours = sorted({t.replace(minute=0, second=0, microsecond=0) for t in times})
        by_hour = {h: 0 for h in hours}
        bad_by_hour = {h: 0 for h in hours}
        for meta in photos:
            if meta.exif_source != "exif":
                continue
            hour = meta.datetime_original.replace(minute=0, second=0, microsecond=0)
            by_hour[hour] += 1
            if flagged is not None and meta.path in flagged:
                bad_by_hour[hour] += 1
        multi_day = len({h.date() for h in hours}) > 1
        multi_year = len({h.year for h in hours}) > 1
        fmt = "%Y-%m-%d %H시" if multi_year else "%m-%d %H시" if multi_day else "%H시"
        stats.sections.append(Section("시간대", [
            Bucket(h.strftime(fmt), n,
                   f"C컷 {bad_by_hour[h]}" if flagged is not None and bad_by_hour[h] else "")
            for h, n in by_hour.items()
        ]))

    isos = [v for v in (_number(m.iso) for m in photos) if v]
    if isos:
        stats.sections.append(Section("ISO", _binned(isos, [
            (100, "100 이하"), (200, "200"), (400, "400"), (800, "800"), (1600, "1600"), (3200, "3200"), (6400, "6400"),
        ], "6400 넘음")))
    shutters = [v for v in (_number(m.shutter_speed) for m in photos) if v]
    if shutters:
        stats.sections.append(Section("셔터속도", _binned(shutters, [
            (1 / 1000, "1/1000 이상 빠름"), (1 / 500, "1/500"), (1 / 250, "1/250"), (1 / 125, "1/125"),
            (1 / 60, "1/60"), (1 / 30, "1/30"),
        ], "1/30보다 느림"), "느린 셔터(1/60 이하)는 흔들리기 쉬워요."))
    apertures = [v for v in (_number(m.aperture) for m in photos) if v]
    if apertures:
        stats.sections.append(Section("조리개", _binned(apertures, [
            (1.4, "f/1.4 이하"), (2.0, "f/2"), (2.8, "f/2.8"), (4.0, "f/4"), (5.6, "f/5.6"), (8.0, "f/8"),
        ], "f/8보다 조임")))
    focals = [m.focal_length for m in photos if m.focal_length]
    if focals:
        stats.sections.append(Section("초점거리", _binned(focals, [
            (24, "24mm 이하"), (35, "25~35mm"), (50, "36~50mm"), (85, "51~85mm"), (135, "86~135mm"), (200, "136~200mm"),
        ], "200mm 넘음")))
    return stats


def as_text(stats: Stats) -> str:
    """복사해 붙여넣을 글."""
    lines = [f"사진 {stats.total}장"]
    if stats.first:
        lines[0] += f" · {stats.first:%Y-%m-%d %H:%M} ~ {stats.last:%Y-%m-%d %H:%M}"
    for section in stats.sections:
        total = sum(b.count for b in section.buckets) or 1
        lines.append(f"\n[{section.title}]")
        for bucket in section.buckets:
            lines.append(f"  {bucket.label}: {bucket.count}장 ({bucket.count * 100 // total}%)"
                         + (f" · {bucket.extra}" if bucket.extra else ""))
        if section.note:
            lines.append(f"  ※ {section.note}")
    return "\n".join(lines)
