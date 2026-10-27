"""M7: test_photos/의 현재 파이프라인 결과를 회귀 테스트 기준선으로 저장한다.

test_photos/에 실사진을 넣거나 점수 계산을 바꾼 뒤 실행하면:
  1) 지금 결과를 계산해 예전 기준선(test_photos/baseline.json)과 비교해 보여주고,
  2) 바뀐 장면만 사진으로 나란히 보여주는 검토 페이지(test_photos/baseline_review.html)를
     만든 뒤(기준선이 없으면 모든 장면),
  3) 사람이 보고 "지금 결과가 낫다"고 판단해 y를 누를 때만 기준선을 바꾼다.

    python tests/update_regression_baseline.py          # 비교 + 검토 페이지 + 물어보고 저장
    python tests/update_regression_baseline.py --check  # 비교 + 검토 페이지만(저장 안 함)
    python tests/update_regression_baseline.py --yes    # 묻지 않고 저장

CLAUDE.md 개발 원칙: "로직을 수정한 뒤에는 이 샘플로 결과가 나빠지지 않았는지 비교한다" —
그 비교 대상이 이 기준선이다. test_photos/는 개인 사진이 들어갈 수 있어 통째로
gitignore돼 있으므로(baseline.json·검토 페이지 포함), 이 기준선은 각자 로컬에서만 의미가 있다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from baseline_review import diff_selection, format_diff, write_review_html  # noqa: E402
from grouping import group_by_time_gap  # noqa: E402
from report import ReportRow, build_report  # noqa: E402
from scanner import scan_and_extract  # noqa: E402

TEST_PHOTOS_DIR = Path(__file__).resolve().parent.parent / "test_photos"
BASELINE_PATH = TEST_PHOTOS_DIR / "baseline.json"
REVIEW_PATH = TEST_PHOTOS_DIR / "baseline_review.html"


def compute_rows(test_photos_dir: Path) -> list[ReportRow]:
    results = scan_and_extract(test_photos_dir, recursive=True, include_unpaired_raw=True)
    return build_report(group_by_time_gap(results))


def selection_of(rows: list[ReportRow], test_photos_dir: Path) -> dict[str, bool]:
    return {str(Path(row.path).relative_to(test_photos_dir)): row.selected for row in rows}


def compute_selection(test_photos_dir: Path) -> dict[str, bool]:
    """test_photos_dir 기준 상대경로 → 선택 여부(dict)를 계산한다."""
    return selection_of(compute_rows(test_photos_dir), test_photos_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description="회귀 기준선 비교·검토·갱신")
    parser.add_argument("--check", action="store_true", help="비교와 검토 페이지만(저장 안 함)")
    parser.add_argument("--yes", action="store_true", help="묻지 않고 기준선 저장")
    args = parser.parse_args()

    photo_files = [
        p for p in TEST_PHOTOS_DIR.rglob("*")
        if p.is_file() and p.name not in (".gitkeep", BASELINE_PATH.name, REVIEW_PATH.name)
    ]
    if not photo_files:
        print(f"{TEST_PHOTOS_DIR}에 사진이 없습니다. 먼저 실사진을 넣어주세요.")
        return

    print("지금 결과를 계산하는 중...")
    rows = compute_rows(TEST_PHOTOS_DIR)
    selection = selection_of(rows, TEST_PHOTOS_DIR)
    old = json.loads(BASELINE_PATH.read_text(encoding="utf-8")) if BASELINE_PATH.exists() else None

    if old is not None:
        diff = diff_selection(old, selection)
        print(format_diff(diff))
        if not diff.changed:
            return
    scenes = write_review_html(rows, old, TEST_PHOTOS_DIR, REVIEW_PATH)
    print(f"검토 페이지: {REVIEW_PATH} (장면 {scenes}개) — 브라우저로 열어 확인하세요.")

    if args.check:
        return
    if not args.yes:
        if not sys.stdin.isatty():
            print("기준선은 그대로 둡니다(확인 후 --yes로 저장).")
            return
        answer = input("지금 결과로 기준선을 바꿀까요? [y/N] ").strip().lower()
        if answer not in ("y", "yes", "ㅛ"):
            print("기준선은 그대로 둡니다.")
            return
    BASELINE_PATH.write_text(
        json.dumps(selection, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    selected = sum(selection.values())
    print(f"기준선 저장: {BASELINE_PATH} ({selected}/{len(selection)}장 선택)")


if __name__ == "__main__":
    main()
