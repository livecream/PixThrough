"""M7: test_photos/의 현재 파이프라인 결과를 회귀 테스트 기준선으로 저장한다.

test_photos/에 실사진을 넣거나 바꾼 뒤, `python src/cli.py select --input test_photos
--recursive` 등으로 선정 결과를 사람이 직접 검토해서 만족스러우면 이 스크립트로
기준선(test_photos/baseline.json)을 갱신한다. CLAUDE.md 개발 원칙: "로직을 수정한
뒤에는 이 샘플로 결과가 나빠지지 않았는지 비교한다" — 그 비교 대상이 이 기준선이다.

test_photos/는 개인 사진이 들어갈 수 있어 통째로 gitignore돼 있으므로(baseline.json
포함), 이 기준선은 각자 로컬에서만 의미가 있다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from grouping import group_by_time_gap  # noqa: E402
from report import build_report  # noqa: E402
from scanner import scan_and_extract  # noqa: E402

TEST_PHOTOS_DIR = Path(__file__).resolve().parent.parent / "test_photos"
BASELINE_PATH = TEST_PHOTOS_DIR / "baseline.json"


def compute_selection(test_photos_dir: Path) -> dict[str, bool]:
    """test_photos_dir 기준 상대경로 → 선택 여부(dict)를 계산한다."""
    results = scan_and_extract(test_photos_dir, recursive=True, include_unpaired_raw=True)
    groups = group_by_time_gap(results)
    rows = build_report(groups)
    return {
        str(Path(row.path).relative_to(test_photos_dir)): row.selected for row in rows
    }


def main() -> None:
    photo_files = [p for p in TEST_PHOTOS_DIR.rglob("*") if p.is_file() and p.name != ".gitkeep"]
    if not photo_files:
        print(f"{TEST_PHOTOS_DIR}에 사진이 없습니다. 먼저 실사진을 넣어주세요.")
        return

    selection = compute_selection(TEST_PHOTOS_DIR)
    BASELINE_PATH.write_text(
        json.dumps(selection, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    selected = sum(selection.values())
    print(f"기준선 저장: {BASELINE_PATH} ({selected}/{len(selection)}장 선택)")


if __name__ == "__main__":
    main()
