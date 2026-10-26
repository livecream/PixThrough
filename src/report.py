"""M5: dry-run 리포트.

PROJECT_SPEC.md 4.5절: 실제 파일 작업(M6) 전에 어떤 사진이 왜 선택/제외됐는지
CSV 또는 JSON으로 먼저 보여준다. 이 모듈은 리포트 생성까지만 하고 파일 복사/이동은
하지 않는다.
"""

from __future__ import annotations

import csv
import datetime
import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Callable

from scanner import PhotoMetadata
from selector import SceneSelection, select_best


@dataclass(frozen=True)
class ReportRow:
    path: Path
    scene_id: int
    sharpness: float
    exposure: float
    face: float | None
    total: float
    selected: bool
    reason: str
    # 선명도를 어디서 쟀는지(얼굴/칸)와 예전 방식(사진 전체) 값 — 리포트 비교용
    sharpness_region: str | None = None
    sharpness_global: float | None = None
    # 사용자가 미리보기에서 직접 바꾼 경우(MANUAL_ADDED/MANUAL_REMOVED), 자동 그대로면 None
    manual_choice: str | None = None
    # C컷으로 판정된 사유(예: "블러(선명도 낮음)"), C컷이 아니면 None. 사진 선별 화면에서
    # C컷 이동 대상과 빨간 표시에 쓴다(직접 넣은 컷은 옮기지 않음).
    badcut_reason: str | None = None


MANUAL_ADDED = "added"
MANUAL_REMOVED = "removed"


def _reason_for(index: int, selection: SceneSelection, keep_percent: int) -> str:
    if index == selection.best_index:
        return "선택됨: 장면 내 최고 종합점수"
    if index in selection.kept_indices:
        rank = selection.kept_indices.index(index) + 1
        size = len(selection.scene)
        kept = len(selection.kept_indices)
        return f"선택됨: 장면 내 {rank}위 (장면 {size}장의 {keep_percent}% 올림 → {kept}장 남김)"

    score = selection.scores[index]
    if score.error:
        return f"제외: 점수 계산 실패 ({score.error})"
    if index in selection.excluded:
        return f"제외: C컷 — {selection.excluded[index]}"
    lowest_kept = selection.combined_scores[selection.kept_indices[-1]]
    if selection.combined_scores[index] == lowest_kept:
        return "제외: 동점이며 더 늦게 촬영됨"
    return "제외: 종합 점수가 더 낮음"


def apply_manual_choices(rows: list[ReportRow], choices: dict[Path, bool]) -> list[ReportRow]:
    """자동 선정 결과(rows)에 사용자가 직접 고른 것(choices: 경로 → 선택 여부)을 덮어쓴다.

    자동 선정은 장면당 1장이지만, 사용자는 장면마다 원하는 컷을 더 넣거나 자동으로
    고른 컷을 뺄 수 있다. 자동 결과와 같은 선택(이미 자동으로 고른 컷을 "선택")은 바뀌는
    게 없으므로 그대로 둔다 — 그래서 장면 간격을 바꿔 자동 결과가 달라져도 경로 기준으로
    직접 고른 것이 유지된다. 왜 선택/제외됐는지 리포트·로그에 남도록 사유도 바꾼다.
    """
    result = []
    for row in rows:
        wanted = choices.get(row.path)
        if wanted is None or wanted == row.selected:
            result.append(row)
        elif wanted:
            result.append(
                replace(
                    row,
                    selected=True,
                    manual_choice=MANUAL_ADDED,
                    reason="선택됨: 직접 추가 (자동 선택: " + row.reason + ")",
                )
            )
        else:
            result.append(
                replace(
                    row,
                    selected=False,
                    manual_choice=MANUAL_REMOVED,
                    reason="제외: 직접 뺌 (자동으로는 선택됐던 컷)",
                )
            )
    return result


def build_report(
    groups: list[list[PhotoMetadata]],
    on_progress: Callable[[int, int], None] | None = None,
    cache: dict | None = None,
    keep_percent: int = 0,
    exclude: Callable | None = None,
) -> list[ReportRow]:
    """장면 그룹 목록으로부터 사진별 선택/제외 리포트 행을 만든다.
    keep_percent: 장면마다 남길 비율(%), 올림·최소 1장 (selector.keep_count).
    exclude: 점수 → 후보에서 뺄 사유(C컷 판정, badcut.badcut_reason) 또는 None.

    on_progress가 주어지면 전체 장면을 통틀어 사진 1장 처리할 때마다
    (누적 완료 수, 전체 수)를 알려준다 (장면마다 리셋되지 않음).
    """
    rows: list[ReportRow] = []
    total = sum(len(group) for group in groups)
    done = 0

    for scene_id, group in enumerate(groups, start=1):
        scene_progress = None
        if on_progress is not None:
            base = done

            def scene_progress(i: int, _n: int, base: int = base) -> None:
                on_progress(base + i, total)

        selection = select_best(
            group, on_progress=scene_progress, cache=cache, keep_percent=keep_percent, exclude=exclude
        )
        done += len(group)

        for i, meta in enumerate(group):
            score = selection.scores[i]
            rows.append(
                ReportRow(
                    path=meta.path,
                    scene_id=scene_id,
                    sharpness=score.sharpness,
                    exposure=score.exposure,
                    face=score.face,
                    total=selection.combined_scores[i],
                    selected=i in selection.kept_indices,
                    reason=_reason_for(i, selection, keep_percent),
                    sharpness_region=score.sharpness_region,
                    sharpness_global=score.sharpness_global,
                    badcut_reason=selection.excluded.get(i),
                )
            )
    return rows


def _row_to_dict(row: ReportRow) -> dict:
    d = asdict(row)
    d["path"] = str(row.path)
    return d


def write_report(rows: list[ReportRow], output_path: Path) -> None:
    """확장자(.csv 또는 .json)에 따라 리포트를 파일로 쓴다."""
    output_path = Path(output_path)
    suffix = output_path.suffix.lower()

    if suffix == ".csv":
        # utf-8-sig(파일 앞에 BOM): Excel은 BOM이 없으면 UTF-8 CSV를 시스템 인코딩으로 읽어
        # 한글 사유·파일명이 깨진다. Numbers·Google 시트·Python csv(utf-8-sig)도 그대로 읽는다.
        with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
            fieldnames = list(_row_to_dict(rows[0]).keys()) if rows else []
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(_row_to_dict(row))
    elif suffix == ".json":
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump([_row_to_dict(r) for r in rows], f, ensure_ascii=False, indent=2)
    else:
        raise ValueError(f"지원하지 않는 리포트 형식입니다: {suffix} (.csv 또는 .json만 지원)")


def write_apply_report(rows: list[ReportRow], output_dir: Path, kind: str) -> Path:
    """GUI에서 적용(복사/이동)한 뒤, 그때 화면에 있던 판정 전체(선택·제외된 사진 모두의
    장면·점수·사유)를 출력 폴더에 CSV로 남긴다. CLI의 report와 같은 형식이라, 적용 로그
    (apply_log_*.jsonl, 옮긴 파일만 기록)와 함께 보면 왜 이 사진들이 골라졌는지 나중에도
    확인할 수 있다. kind는 파일 이름 앞부분("bestcut"/"badcut")."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{kind}_report_{datetime.datetime.now():%Y%m%d_%H%M%S}.csv"
    write_report(rows, path)
    return path
