"""브라케팅(노출 연사, AEB) 묶음 찾기. 사용자 결정: 표시 + "통째로 남기기" 옵션(기본 꺼짐).

같은 바디로 BRACKET_GAP_SECONDS 안에 이어 찍었고 노출 보정(EXIF ExposureBiasValue)이 서로 다른
사진이 MIN_SHOTS장 이상 이어지면 한 묶음이다(예: 0 · −2 · +2 EV). 노출 보정을 손으로 바꿔 가며 찍는
경우는 보통 몇 초 넘게 걸려 묶이지 않는다. 노출 보정을 못 읽는 사진(CR3만 등)은 묶지 않는다.

옵션을 켜면 묶음의 사진(점수를 못 낸 사진 빼고)을 전부 남긴다(HDR 합성용) — keep_brackets.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from clock_sync import camera_key
from report import ReportRow
from scanner import PhotoMetadata

BRACKET_GAP_SECONDS = 2.0  # 묶음 안 사진 사이 간격(AEB는 연사라 보통 1초 미만)
MIN_SHOTS = 3
MAX_SHOTS = 9


@dataclass(frozen=True)
class Bracket:
    number: int  # 1부터
    position: int  # 묶음 안 순서(1부터)
    size: int
    evs: tuple[float, ...]  # 묶음의 노출 보정값(찍은 순서)

    def describe(self) -> str:
        values = " · ".join(_format_ev(ev) for ev in self.evs)
        return f"브라케팅 {self.number} ({self.position}/{self.size}장, {values} EV)"


def _format_ev(ev: float) -> str:
    if abs(ev) < 0.005:
        return "0"
    text = f"{ev:+.1f}".rstrip("0").rstrip(".")
    return text.replace("-", "−")


def find_brackets(photos: list[PhotoMetadata]) -> dict:
    """사진 경로 → Bracket. 촬영 시각 순서로 바디마다 훑는다."""
    by_body: dict[str | None, list[PhotoMetadata]] = {}
    for meta in photos:
        if meta.exposure_bias is not None and meta.error is None:
            by_body.setdefault(camera_key(meta), []).append(meta)
    runs: list[list[PhotoMetadata]] = []
    for body_photos in by_body.values():
        body_photos.sort(key=lambda m: (m.datetime_original, str(m.path)))
        run: list[PhotoMetadata] = []
        for meta in body_photos:
            if run:
                gap = (meta.datetime_original - run[-1].datetime_original).total_seconds()
                evs = {m.exposure_bias for m in run}
                if gap <= BRACKET_GAP_SECONDS and meta.exposure_bias not in evs and len(run) < MAX_SHOTS:
                    run.append(meta)
                    continue
                if len(run) >= MIN_SHOTS:
                    runs.append(run)
            run = [meta]
        if len(run) >= MIN_SHOTS:
            runs.append(run)
    runs.sort(key=lambda r: r[0].datetime_original)
    brackets = {}
    for number, run in enumerate(runs, start=1):
        evs = tuple(m.exposure_bias for m in run)
        for position, meta in enumerate(run, start=1):
            brackets[meta.path] = Bracket(number, position, len(run), evs)
    return brackets


def keep_brackets(rows: list[ReportRow], brackets: dict) -> list[ReportRow]:
    """브라케팅 묶음의 사진을 전부 남긴다(점수를 못 낸 사진은 빼고). 다른 사진은 그대로."""
    result = []
    for row in rows:
        bracket = brackets.get(row.path)
        if bracket is not None and not row.selected and not row.reason.startswith("제외: 점수 계산 실패"):
            row = replace(
                row, selected=True,
                reason=f"선택됨: 브라케팅 묶음 통째로 남김 — {bracket.describe()}",
            )
        result.append(row)
    return result
