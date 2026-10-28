"""CLI 진입점. `scan`(M1) + `group`(M2) + `score`(M3) + `select`(M4) + `report`(M5) +
`apply`(M6) + `rename`(M9) + `badcut`(M10) + `resize`(사진 축소) + `proof`(고객 셀렉).

group/select/report/apply/rename은 `--clock-offset "바디 이름=시차"`(여러 번)로 바디별 시계를
맞출 수 있다(clock_sync). 바디 이름은 `scan` 결과처럼 "Canon EOS R5 #시리얼"."""

from __future__ import annotations

import argparse
from pathlib import Path

import app_log
from badcut import BadCutThresholds, build_badcut_rows
from clock_sync import parse_offset, shift_photos, summarize
from file_ops import apply_selection
from grouping import DEFAULT_SCENE_GAP_SECONDS, group_scenes
from proof_gallery import LONG_EDGE_START, build_proof, copy_selection, list_photos, match_selection, parse_selection
from rename_by_time import (
    DEFAULT_DIGITS,
    apply_merge_plan,
    apply_rename_plan,
    build_merge_plan,
    build_rename_plan,
    write_rename_report,
)
from report import build_report, write_report
from resize import ACTION_ERROR, ACTION_LABELS, apply_resize_plan, build_resize_plan
from scanner import scan_and_extract
from scoring import score_photos
from selector import select_best
from xmp_rating import ACTION_SKIPPED, default_run_dir, plan_xmp, write_xmp


def _percent_arg(value: str) -> int:
    percent = int(value)
    if not 0 <= percent <= 100:
        raise argparse.ArgumentTypeError("0~100 사이의 정수여야 합니다")
    return percent


def _clock_offsets(args: argparse.Namespace) -> dict[str, float]:
    offsets: dict[str, float] = {}
    for item in getattr(args, "clock_offset", None) or []:
        name, sep, value = item.rpartition("=")
        if not sep or not name.strip():
            raise SystemExit(f"--clock-offset 형식: \"바디 이름=+1:23\" (받은 값: {item})")
        offsets[name.strip()] = parse_offset(value)
    return offsets


def _scan(args: argparse.Namespace):
    """사진을 스캔하고, --clock-offset이 있으면 바디별 시차를 더해 다시 정렬한다."""
    results = scan_and_extract(Path(args.input), recursive=args.recursive, include_unpaired_raw=True)
    return shift_photos(results, _clock_offsets(args))


def cmd_scan(args: argparse.Namespace) -> None:
    results = scan_and_extract(Path(args.input), recursive=args.recursive, include_unpaired_raw=True)
    print(f"총 {len(results)}개 파일 발견 (입력: {args.input}, 하위폴더 포함: {args.recursive})\n")
    for m in results:
        marker = "  [EXIF 없음, mtime 사용]" if m.exif_source == "mtime" else ""
        err = f"  [오류: {m.error}]" if m.error else ""
        print(f"{m.datetime_original}  {m.path.name}  ({m.width}x{m.height}){marker}{err}")
    cameras, unknown = summarize(results)
    if cameras:
        print("\n바디(--clock-offset에 쓰는 이름):")
        for camera in cameras:
            print(f"  {camera.key}: {camera.count}장, {camera.first} ~ {camera.last}")
        if unknown:
            print(f"  (카메라 정보 없음 {unknown}개)")


def cmd_group(args: argparse.Namespace) -> None:
    results = _scan(args)
    groups = group_scenes(
        results,
        gap_seconds=args.scene_gap_seconds,
        max_hash_distance=args.split_hash_distance,
    )
    print(
        f"총 {len(results)}개 파일 → {len(groups)}개 장면 그룹 "
        f"(간격: {args.scene_gap_seconds}초"
        + (
            f", 앞 사진과 차이 {args.split_hash_distance} 초과 시 나눔"
            if args.split_hash_distance is not None
            else ""
        )
        + ")\n"
    )
    for i, group in enumerate(groups, start=1):
        names = ", ".join(m.path.name for m in group)
        print(f"[장면 {i}] {len(group)}장: {names}")


def cmd_score(args: argparse.Namespace) -> None:
    results = scan_and_extract(Path(args.input), recursive=args.recursive, include_unpaired_raw=True)
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
    results = _scan(args)
    groups = group_scenes(
        results,
        gap_seconds=args.scene_gap_seconds,
        max_hash_distance=args.split_hash_distance,
    )
    print(f"총 {len(groups)}개 장면 그룹에서 베스트 컷 선정\n")
    for i, group in enumerate(groups, start=1):
        selection = select_best(group, keep_percent=args.keep_percent)
        kept = [group[k].path.name for k in selection.kept_indices]
        others = ", ".join(m.path.name for k, m in enumerate(group) if k not in selection.kept_indices)
        chosen = ", ".join(kept) if kept else "없음 (점수를 계산한 사진이 없음)"
        print(f"[장면 {i}] 선택: {chosen}" + (f"  (제외: {others})" if others else ""))


def cmd_report(args: argparse.Namespace) -> None:
    results = _scan(args)
    groups = group_scenes(
        results,
        gap_seconds=args.scene_gap_seconds,
        max_hash_distance=args.split_hash_distance,
    )
    rows = build_report(groups, keep_percent=args.keep_percent)
    write_report(rows, Path(args.output))
    selected = sum(1 for r in rows if r.selected)
    print(
        f"{len(groups)}개 장면, {len(rows)}개 파일 중 {selected}장 선택 "
        f"→ 리포트 저장: {args.output} (dry-run, 실제 파일 작업 없음)"
    )


def cmd_apply(args: argparse.Namespace) -> None:
    results = _scan(args)
    groups = group_scenes(
        results,
        gap_seconds=args.scene_gap_seconds,
        max_hash_distance=args.split_hash_distance,
    )
    rows = build_report(groups, keep_percent=args.keep_percent)
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

    offsets = _clock_offsets(args)
    if merge_output is not None:
        rows = build_merge_plan(
            input_dirs, merge_output, recursive=args.recursive, digits=args.digits, offsets=offsets
        )
    else:
        rows = build_rename_plan(input_dirs, recursive=args.recursive, digits=args.digits, offsets=offsets)
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
    photos = scan_and_extract(Path(args.input), recursive=args.recursive, include_unpaired_raw=True)
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


def cmd_resize(args: argparse.Namespace) -> None:
    output = Path(args.output)
    plan = build_resize_plan(Path(args.input), output, args.long_edge, recursive=args.recursive)
    for row in plan.rows:
        size = (
            f"{row.original_size[0]}×{row.original_size[1]} → {row.new_size[0]}×{row.new_size[1]}"
            if row.original_size and row.new_size
            else row.error
        )
        print(f"{row.source} → {row.destination.name}: {ACTION_LABELS[row.action]} ({size})")
    todo = sum(1 for r in plan.rows if r.action != ACTION_ERROR)
    if not args.apply:
        print(
            f"[dry-run] {todo}장 저장 예정 → {output} (RAW {plan.skipped_raw}개 건너뜀)\n"
            "실제로 저장하려면 --apply를 추가하세요."
        )
        return
    results = apply_resize_plan(plan.rows, output, args.long_edge, args.quality)
    failed = [r for r in results if r.error]
    print(f"완료: {len(results) - len(failed)}장 저장 → {output}" + (f", 실패 {len(failed)}장" if failed else ""))


def cmd_proof_make(args: argparse.Namespace) -> None:
    folder, output = Path(args.input), Path(args.output)
    title = args.title or folder.name
    if not args.apply:
        items = list_photos(folder, args.recursive)
        for photo_id, _ in items:
            print(photo_id)
        print(
            f"[dry-run] {len(items)}장으로 고객 셀렉 페이지를 만들 예정 → {output / title} (+ zip)\n"
            "실제로 만들려면 --apply를 추가하세요."
        )
        return
    result = build_proof(
        folder, output, title, long_edge=args.long_edge, watermark=args.watermark,
        max_picks=args.limit, recursive=args.recursive,
    )
    for path, error in result.failed:
        print(f"건너뜀: {path} — {error}")
    print(f"완료: {len(result.photos)}장 → {result.package_dir} (zip: {result.zip_path})")


def cmd_proof_import(args: argparse.Namespace) -> None:
    folder = Path(args.input)
    selection = parse_selection(Path(args.result).read_text(encoding="utf-8-sig"))
    matched, missing = match_selection(selection, folder, args.recursive)
    for pick in matched:
        print(f"{pick.source}" + (f" — {pick.note}" if pick.note else ""))
    for name in missing:
        print(f"못 찾음: {name}")
    if selection.memo:
        print(f"고객 메모: {selection.memo}")
    if not args.apply:
        print(
            f"[dry-run] {len(matched)}장 복사 예정 → {args.output} (못 찾음 {len(missing)}개)\n"
            "실제로 복사하려면 --apply를 추가하세요."
        )
        return
    results, report = copy_selection(matched, Path(args.output), folder)
    print(f"복사 완료: {len(results)}장 → {args.output} (목록: {report})")
    if args.label:
        run_dir = default_run_dir(app_log.app_data_dir())
        written = write_xmp(plan_xmp([], [], [pick.source for pick in matched]), 0, run_dir, label=args.label)
        done = sum(1 for r in written if r.action != ACTION_SKIPPED)
        print(f"라이트룸 색 라벨 \"{args.label}\": {done}개 .xmp (RAW 없는 사진 건너뜀, 백업·기록: {run_dir})")


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
    p_group.add_argument(
        "--split-hash-distance",
        type=int,
        default=None,
        dest="split_hash_distance",
        help="같은 장면 안에서 바로 앞 사진과 이미지 지문 차이(0~64)가 이 값을 넘으면 새 장면으로 "
        "나눔. 생략하면 시간 간격으로만 묶음",
    )
    p_group.add_argument(
        "--clock-offset",
        action="append",
        dest="clock_offset",
        metavar="바디=시차",
        help="바디별 시계 맞추기(여러 번): \"Canon EOS R5 #123=+1:23\" — 바디 이름은 scan 결과 참고",
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
    p_select.add_argument(
        "--split-hash-distance",
        type=int,
        default=None,
        dest="split_hash_distance",
        help="같은 장면 안에서 바로 앞 사진과 이미지 지문 차이(0~64)가 이 값을 넘으면 새 장면으로 "
        "나눔. 생략하면 시간 간격으로만 묶음",
    )
    p_select.add_argument(
        "--keep-percent",
        type=_percent_arg,
        default=0,
        dest="keep_percent",
        help="장면마다 남길 비율(0~100%%). 장면 사진 수 × 비율을 올림, 최소 1장. "
        "기본 0 = 장면마다 1장",
    )
    p_select.add_argument(
        "--clock-offset",
        action="append",
        dest="clock_offset",
        metavar="바디=시차",
        help="바디별 시계 맞추기(여러 번): \"Canon EOS R5 #123=+1:23\" — 바디 이름은 scan 결과 참고",
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
        "--split-hash-distance",
        type=int,
        default=None,
        dest="split_hash_distance",
        help="같은 장면 안에서 바로 앞 사진과 이미지 지문 차이(0~64)가 이 값을 넘으면 새 장면으로 "
        "나눔. 생략하면 시간 간격으로만 묶음",
    )
    p_report.add_argument(
        "--keep-percent",
        type=_percent_arg,
        default=0,
        dest="keep_percent",
        help="장면마다 남길 비율(0~100%%). 장면 사진 수 × 비율을 올림, 최소 1장. "
        "기본 0 = 장면마다 1장",
    )
    p_report.add_argument(
        "--output", required=True, help="리포트 저장 경로 (.csv 또는 .json)"
    )
    p_report.add_argument(
        "--clock-offset",
        action="append",
        dest="clock_offset",
        metavar="바디=시차",
        help="바디별 시계 맞추기(여러 번): \"Canon EOS R5 #123=+1:23\" — 바디 이름은 scan 결과 참고",
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
    p_apply.add_argument(
        "--split-hash-distance",
        type=int,
        default=None,
        dest="split_hash_distance",
        help="같은 장면 안에서 바로 앞 사진과 이미지 지문 차이(0~64)가 이 값을 넘으면 새 장면으로 "
        "나눔. 생략하면 시간 간격으로만 묶음",
    )
    p_apply.add_argument(
        "--keep-percent",
        type=_percent_arg,
        default=0,
        dest="keep_percent",
        help="장면마다 남길 비율(0~100%%). 장면 사진 수 × 비율을 올림, 최소 1장. "
        "기본 0 = 장면마다 1장",
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
    p_apply.add_argument(
        "--clock-offset",
        action="append",
        dest="clock_offset",
        metavar="바디=시차",
        help="바디별 시계 맞추기(여러 번): \"Canon EOS R5 #123=+1:23\" — 바디 이름은 scan 결과 참고",
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
    p_rename.add_argument(
        "--clock-offset",
        action="append",
        dest="clock_offset",
        metavar="바디=시차",
        help="바디별 시계 맞추기(여러 번): \"Canon EOS R5 #123=+1:23\" — 바디 이름은 scan 결과 참고",
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

    p_resize = sub.add_parser(
        "resize", help="사진을 긴 변 기준 픽셀로 줄여 JPG로 새 폴더에 저장 (원본 그대로, RAW 제외)"
    )
    p_resize.add_argument("--input", required=True, help="축소할 사진 폴더")
    p_resize.add_argument("--output", required=True, help="축소본을 저장할 폴더(입력 폴더와 겹치면 안 됨)")
    p_resize.add_argument("--long-edge", type=int, required=True, dest="long_edge", help="긴 변 픽셀")
    p_resize.add_argument("--quality", type=int, default=90, help="JPG 화질 1~100, 기본값 90")
    p_resize.add_argument("--recursive", action="store_true", help="하위 폴더까지(같은 구조로 저장)")
    p_resize.add_argument(
        "--apply", action="store_true", help="실제로 저장. 없으면 dry-run(계획만 출력)으로 끝남"
    )
    p_resize.set_defaults(func=cmd_resize)

    p_proof = sub.add_parser("proof", help="고객 셀렉: 고를 수 있는 페이지 만들기 / 고객 선택 가져오기")
    proof_sub = p_proof.add_subparsers(dest="proof_command", required=True)
    p_make = proof_sub.add_parser("make", help="사진 폴더로 고객 셀렉 페이지(폴더+zip) 만들기")
    p_make.add_argument("--input", required=True, help="고객에게 보여줄 사진 폴더")
    p_make.add_argument("--output", required=True, help="페이지를 만들 폴더(사진 폴더와 겹치면 안 됨)")
    p_make.add_argument("--title", default="", help="페이지 제목(폴더·zip 이름). 기본값은 사진 폴더 이름")
    p_make.add_argument(
        "--long-edge", type=int, default=LONG_EDGE_START, dest="long_edge",
        help=f"페이지 사진 긴 변 픽셀, 기본값 {LONG_EDGE_START}",
    )
    p_make.add_argument("--watermark", default="", help="사진 가운데 넣을 글자(없으면 넣지 않음)")
    p_make.add_argument("--limit", type=int, default=0, help="고를 수 있는 최대 장 수 안내(0 = 제한 없음)")
    p_make.add_argument("--recursive", action="store_true", help="하위 폴더까지")
    p_make.add_argument("--apply", action="store_true", help="실제로 만들기. 없으면 dry-run(대상 사진만 출력)")
    p_make.set_defaults(func=cmd_proof_make)
    p_import = proof_sub.add_parser("import", help="고객 선택 결과(JSON 또는 이름 목록)로 원본을 따로 복사")
    p_import.add_argument("--input", required=True, help="원본 사진 폴더(페이지를 만든 폴더)")
    p_import.add_argument("--result", required=True, help="고객이 보낸 결과 파일(.json) 또는 이름 목록(.txt)")
    p_import.add_argument("--output", required=True, help="고른 사진을 복사할 폴더")
    p_import.add_argument("--recursive", action="store_true", help="하위 폴더까지")
    p_import.add_argument(
        "--label", default="",
        help="--apply와 함께: 고객이 고른 사진의 RAW 옆 .xmp에 라이트룸 색 라벨(예: Green)도 씀",
    )
    p_import.add_argument("--apply", action="store_true", help="실제로 복사. 없으면 dry-run(맞춘 결과만 출력)")
    p_import.set_defaults(func=cmd_proof_import)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
