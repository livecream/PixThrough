"""CLI 진입점. `scan`(M1) + `group`(M2) + `score`(M3) + `select`(M4) + `report`(M5) +
`apply`(M6) + `rename`(M9) + `badcut`(M10) + `faces`(얼굴 검출 진단)."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from badcut import BadCutThresholds, build_badcut_rows
from file_ops import apply_selection
from grouping import DEFAULT_SCENE_GAP_SECONDS, group_by_time_gap
from rename_by_time import (
    DEFAULT_DIGITS,
    apply_merge_plan,
    apply_rename_plan,
    build_merge_plan,
    build_rename_plan,
    write_rename_report,
)
from report import build_report, write_report
from scanner import scan_and_extract
from scoring import FACE_SOURCE_YUNET, diagnose_faces, score_photos
from selector import select_best


def cmd_scan(args: argparse.Namespace) -> None:
    results = scan_and_extract(Path(args.input), recursive=args.recursive)
    print(f"총 {len(results)}개 파일 발견 (입력: {args.input}, 하위폴더 포함: {args.recursive})\n")
    for m in results:
        marker = "  [EXIF 없음, mtime 사용]" if m.exif_source == "mtime" else ""
        err = f"  [오류: {m.error}]" if m.error else ""
        print(f"{m.datetime_original}  {m.path.name}  ({m.width}x{m.height}){marker}{err}")


def cmd_group(args: argparse.Namespace) -> None:
    results = scan_and_extract(Path(args.input), recursive=args.recursive)
    groups = group_by_time_gap(results, gap_seconds=args.scene_gap_seconds)
    print(
        f"총 {len(results)}개 파일 → {len(groups)}개 장면 그룹 "
        f"(간격: {args.scene_gap_seconds}초)\n"
    )
    for i, group in enumerate(groups, start=1):
        names = ", ".join(m.path.name for m in group)
        print(f"[장면 {i}] {len(group)}장: {names}")


def cmd_score(args: argparse.Namespace) -> None:
    results = scan_and_extract(Path(args.input), recursive=args.recursive)
    scores = score_photos(results)
    print(f"총 {len(scores)}개 파일 스코어링 (입력: {args.input})\n")
    for s in scores:
        err = f"  [오류: {s.error}]" if s.error else ""
        face_str = f"{s.face:.2f}" if s.face is not None else "해당없음"
        print(
            f"total={s.total:10.2f}  sharpness={s.sharpness:10.2f}  "
            f"exposure={s.exposure:.2f}  face={face_str}  {s.path.name}{err}"
        )


def cmd_select(args: argparse.Namespace) -> None:
    results = scan_and_extract(Path(args.input), recursive=args.recursive)
    groups = group_by_time_gap(results, gap_seconds=args.scene_gap_seconds)
    print(f"총 {len(groups)}개 장면 그룹에서 베스트 컷 선정\n")
    for i, group in enumerate(groups, start=1):
        selection = select_best(group)
        others = ", ".join(m.path.name for m in group if m.path != selection.best.path)
        print(f"[장면 {i}] 선택: {selection.best.path.name}" + (f"  (제외: {others})" if others else ""))


def cmd_report(args: argparse.Namespace) -> None:
    results = scan_and_extract(Path(args.input), recursive=args.recursive)
    groups = group_by_time_gap(results, gap_seconds=args.scene_gap_seconds)
    rows = build_report(groups)
    write_report(rows, Path(args.output))
    selected = sum(1 for r in rows if r.selected)
    print(
        f"{len(groups)}개 장면, {len(rows)}개 파일 중 {selected}장 선택 "
        f"→ 리포트 저장: {args.output} (dry-run, 실제 파일 작업 없음)"
    )


def cmd_apply(args: argparse.Namespace) -> None:
    results = scan_and_extract(Path(args.input), recursive=args.recursive)
    groups = group_by_time_gap(results, gap_seconds=args.scene_gap_seconds)
    rows = build_report(groups)
    write_report(rows, Path(args.report))

    if not args.apply:
        selected = sum(1 for r in rows if r.selected)
        print(
            f"[dry-run] {len(groups)}개 장면, {selected}장 선택 예정 → 리포트: {args.report}\n"
            f"실제로 파일을 {'이동' if args.move else '복사'}하려면 --apply를 추가하세요."
        )
        return

    op_results = apply_selection(rows, Path(args.output), move=args.move)
    print(
        f"{'이동' if args.move else '복사'} 완료: {len(op_results)}장 "
        f"→ {args.output} (리포트: {args.report})"
    )


def cmd_rename(args: argparse.Namespace) -> None:
    input_dirs = [Path(p) for p in args.input]
    merge_output = Path(args.merge_output) if args.merge_output else None

    if merge_output is not None:
        rows = build_merge_plan(input_dirs, merge_output, recursive=args.recursive, digits=args.digits)
    else:
        rows = build_rename_plan(input_dirs, recursive=args.recursive, digits=args.digits)
    write_rename_report(rows, Path(args.report))

    if not args.apply:
        verb = f"{merge_output}(으)로 병합" if merge_output is not None else "제자리 리네임"
        print(
            f"[dry-run] {len(rows)}개 파일 {verb} 예정 → 리포트: {args.report}\n"
            "실제로 수행하려면 --apply를 추가하세요."
        )
        return

    if merge_output is not None:
        results = apply_merge_plan(rows, merge_output)
    else:
        results = apply_rename_plan(rows, log_dir=Path(args.report).parent)
    print(f"완료: {len(results)}개 파일 (리포트: {args.report})")


def cmd_badcut(args: argparse.Namespace) -> None:
    photos = scan_and_extract(Path(args.input), recursive=args.recursive)
    thresholds = BadCutThresholds(min_sharpness=args.min_sharpness, min_eyes_open=args.min_eyes_open)
    rows = build_badcut_rows(photos, thresholds)
    write_report(rows, Path(args.report))
    bad_count = sum(1 for r in rows if r.selected)

    if not args.apply:
        print(
            f"[dry-run] {len(rows)}장 중 {bad_count}장 C컷 판정 → 리포트: {args.report}\n"
            "실제로 이동하려면 --apply를 추가하세요."
        )
        return

    op_results = apply_selection(rows, Path(args.output), move=True)
    print(f"이동 완료: {len(op_results)}장 → {args.output} (리포트: {args.report})")


_FACES_CSV_FIELDS = [
    "file", "width", "height", "legacy_face_count", "face_index", "source",
    "confidence", "face_width_px", "face_height_px", "eyes_open", "error",
]


def cmd_faces(args: argparse.Namespace) -> None:
    """사진마다 찾은 얼굴(위치·크기·신뢰도·눈뜸)을 CSV로 남기고, 예전 방식(사진 전체를
    FaceLandmarker에)과 몇 장에서 얼굴을 찾았는지 비교해 보여준다. 파일은 건드리지 않는다."""
    photos = scan_and_extract(Path(args.input), recursive=args.recursive)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with_faces = with_legacy = with_eyes = 0
    with output.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=_FACES_CSV_FIELDS)
        writer.writeheader()
        for meta in photos:
            if meta.error is not None:
                writer.writerow({"file": str(meta.path), "error": meta.error})
                continue
            d = diagnose_faces(meta.path)
            base = {
                "file": str(d.path), "width": d.width, "height": d.height,
                "legacy_face_count": len(d.legacy_faces), "error": d.error or "",
            }
            with_faces += bool(d.faces)
            with_legacy += bool(d.legacy_faces)
            with_eyes += any(face.eyes_open is not None for face in d.faces)
            if not d.faces:
                writer.writerow(base)
            for i, face in enumerate(d.faces, start=1):
                x0, y0, x1, y1 = face.bbox
                writer.writerow({
                    **base,
                    "face_index": i,
                    "source": face.source,
                    "confidence": f"{face.confidence:.3f}" if face.confidence is not None else "",
                    "face_width_px": round((x1 - x0) * d.width),
                    "face_height_px": round((y1 - y0) * d.height),
                    "eyes_open": f"{face.eyes_open:.3f}" if face.eyes_open is not None else "",
                })
    print(
        f"총 {len(photos)}장 중 얼굴을 찾은 사진: {with_faces}장 "
        f"(예전 방식 {with_legacy}장), 눈뜸을 판정할 수 있는 사진: {with_eyes}장\n"
        f"얼굴별 결과: {output} (source={FACE_SOURCE_YUNET}이면 새 검출기로 찾은 얼굴, "
        "eyes_open이 빈칸이면 옆얼굴 등이라 눈을 판정하지 못한 얼굴)"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="사진 베스트컷 선별 도구")
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="폴더 스캔 + EXIF 메타데이터 추출 (M1)")
    p_scan.add_argument("--input", required=True, help="스캔할 사진 폴더 경로")
    p_scan.add_argument("--recursive", action="store_true", help="하위 폴더까지 재귀 탐색")
    p_scan.set_defaults(func=cmd_scan)

    p_group = sub.add_parser("group", help="촬영 시각 간격 기준 장면 그룹핑 (M2)")
    p_group.add_argument("--input", required=True, help="스캔할 사진 폴더 경로")
    p_group.add_argument("--recursive", action="store_true", help="하위 폴더까지 재귀 탐색")
    p_group.add_argument(
        "--scene-gap-seconds",
        type=float,
        default=DEFAULT_SCENE_GAP_SECONDS,
        help=f"같은 장면으로 묶을 최대 시간 간격(초), 기본값 {DEFAULT_SCENE_GAP_SECONDS}",
    )
    p_group.set_defaults(func=cmd_group)

    p_score = sub.add_parser("score", help="선명도 기준 품질 스코어링 (M3)")
    p_score.add_argument("--input", required=True, help="스캔할 사진 폴더 경로")
    p_score.add_argument("--recursive", action="store_true", help="하위 폴더까지 재귀 탐색")
    p_score.set_defaults(func=cmd_score)

    p_select = sub.add_parser("select", help="장면별 베스트 컷 선정 (M4)")
    p_select.add_argument("--input", required=True, help="스캔할 사진 폴더 경로")
    p_select.add_argument("--recursive", action="store_true", help="하위 폴더까지 재귀 탐색")
    p_select.add_argument(
        "--scene-gap-seconds",
        type=float,
        default=DEFAULT_SCENE_GAP_SECONDS,
        help=f"같은 장면으로 묶을 최대 시간 간격(초), 기본값 {DEFAULT_SCENE_GAP_SECONDS}",
    )
    p_select.set_defaults(func=cmd_select)

    p_report = sub.add_parser("report", help="선택/제외 사유가 담긴 dry-run 리포트 생성 (M5)")
    p_report.add_argument("--input", required=True, help="스캔할 사진 폴더 경로")
    p_report.add_argument("--recursive", action="store_true", help="하위 폴더까지 재귀 탐색")
    p_report.add_argument(
        "--scene-gap-seconds",
        type=float,
        default=DEFAULT_SCENE_GAP_SECONDS,
        help=f"같은 장면으로 묶을 최대 시간 간격(초), 기본값 {DEFAULT_SCENE_GAP_SECONDS}",
    )
    p_report.add_argument(
        "--output", required=True, help="리포트 저장 경로 (.csv 또는 .json)"
    )
    p_report.set_defaults(func=cmd_report)

    p_apply = sub.add_parser(
        "apply", help="베스트 컷을 실제로 복사/이동 (M6). --apply 없이는 dry-run만 수행"
    )
    p_apply.add_argument("--input", required=True, help="스캔할 사진 폴더 경로")
    p_apply.add_argument("--recursive", action="store_true", help="하위 폴더까지 재귀 탐색")
    p_apply.add_argument(
        "--scene-gap-seconds",
        type=float,
        default=DEFAULT_SCENE_GAP_SECONDS,
        help=f"같은 장면으로 묶을 최대 시간 간격(초), 기본값 {DEFAULT_SCENE_GAP_SECONDS}",
    )
    p_apply.add_argument("--output", required=True, help="베스트 컷을 저장할 폴더")
    p_apply.add_argument(
        "--report", required=True, help="리포트 저장 경로 (.csv 또는 .json)"
    )
    p_apply.add_argument(
        "--move", action="store_true", help="복사 대신 이동 (기본은 복사)"
    )
    p_apply.add_argument(
        "--apply",
        action="store_true",
        help="실제로 파일 작업을 수행. 없으면 dry-run(리포트만 생성)으로 끝남",
    )
    p_apply.set_defaults(func=cmd_apply)

    p_rename = sub.add_parser(
        "rename", help="여러 바디의 사진을 촬영 시각순으로 정렬해 00001부터 제자리 리네임 (M9)"
    )
    p_rename.add_argument(
        "--input", nargs="+", required=True, help="정렬할 사진 폴더(바디별로 여러 개 지정 가능)"
    )
    p_rename.add_argument("--recursive", action="store_true", help="하위 폴더까지 재귀 탐색")
    p_rename.add_argument(
        "--digits", type=int, default=DEFAULT_DIGITS, help=f"번호 자릿수, 기본값 {DEFAULT_DIGITS}"
    )
    p_rename.add_argument("--report", required=True, help="리네임 계획 리포트 저장 경로 (.json)")
    p_rename.add_argument(
        "--merge-output",
        dest="merge_output",
        default=None,
        help="지정하면 제자리 리네임 대신 이 폴더 하나로 모아 이동(+RAW는 하위 RAW/ 폴더)",
    )
    p_rename.add_argument(
        "--apply",
        action="store_true",
        help="실제로 수행. 없으면 dry-run(리포트만 생성)으로 끝남",
    )
    p_rename.set_defaults(func=cmd_rename)

    p_badcut = sub.add_parser(
        "badcut", help="흔들림/눈감음 임계값 미달 사진을 C컷으로 판정해 이동 (M10)"
    )
    p_badcut.add_argument("--input", required=True, help="스캔할 사진 폴더 경로")
    p_badcut.add_argument("--recursive", action="store_true", help="하위 폴더까지 재귀 탐색")
    p_badcut.add_argument(
        "--min-sharpness",
        type=float,
        required=True,
        dest="min_sharpness",
        help="이 값 미만이면 블러(C컷)로 판정",
    )
    p_badcut.add_argument(
        "--min-eyes-open",
        type=float,
        required=True,
        dest="min_eyes_open",
        help="이 값(0~1) 미만이면 눈감음(C컷)으로 판정 (얼굴이 있는 사진에만 적용)",
    )
    p_badcut.add_argument("--output", required=True, help="C컷을 이동할 폴더")
    p_badcut.add_argument("--report", required=True, help="판정 리포트 저장 경로 (.csv 또는 .json)")
    p_badcut.add_argument(
        "--apply",
        action="store_true",
        help="실제로 이동을 수행. 없으면 dry-run(리포트만 생성)으로 끝남",
    )
    p_badcut.set_defaults(func=cmd_badcut)

    p_faces = sub.add_parser(
        "faces", help="얼굴 검출 진단: 사진별 얼굴 위치·크기·눈뜸을 CSV로 저장 (파일 작업 없음)"
    )
    p_faces.add_argument("--input", required=True, help="스캔할 사진 폴더 경로")
    p_faces.add_argument("--recursive", action="store_true", help="하위 폴더까지 재귀 탐색")
    p_faces.add_argument("--output", required=True, help="결과 CSV 저장 경로")
    p_faces.set_defaults(func=cmd_faces)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
