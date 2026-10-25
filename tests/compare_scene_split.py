"""장면 나누기 기준 비교: "비슷하지 않은 사진 나누기"를 켰을 때 기준값마다 장면이
어떻게 나뉘는지 test_photos/(또는 지정한 폴더)의 실사진으로 나란히 보여준다.

어떤 값이 적당한지는 사진(연사인지, 같은 자리에서 구도만 바꾸는지)에 따라 달라 코드에
정해두지 않았다. 이 표로 "같은 장면인데 나뉘었다 / 다른 장면인데 안 나뉘었다"를 보고
베스트컷 탭의 기준값을 고른다. pytest 테스트로는 돌지 않는다(파일 이름이 test_로
시작하지 않음).

    python tests/compare_scene_split.py                 # test_photos/, 간격 3초
    python tests/compare_scene_split.py 폴더경로 --gap 2
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from grouping import (  # noqa: E402
    DEFAULT_SCENE_GAP_SECONDS,
    distances_to_previous,
    group_by_time_gap,
    split_dissimilar,
)
from image_hash import hashes_for  # noqa: E402
from scanner import scan_and_extract  # noqa: E402

TEST_PHOTOS_DIR = Path(__file__).resolve().parent.parent / "test_photos"
THRESHOLDS = [10, 15, 20, 25, 30]


def _scene_numbers(groups) -> dict[Path, int]:
    return {photo.path: i for i, group in enumerate(groups, start=1) for photo in group}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", nargs="?", default=str(TEST_PHOTOS_DIR))
    parser.add_argument("--gap", type=float, default=DEFAULT_SCENE_GAP_SECONDS)
    args = parser.parse_args()
    folder = Path(args.folder)

    photos = scan_and_extract(folder, recursive=True, include_unpaired_raw=True)
    if not photos:
        print(f"사진이 없습니다: {folder}")
        return
    hashes = hashes_for([p.path for p in photos])
    distances = distances_to_previous(photos, hashes)
    time_groups = group_by_time_gap(photos, gap_seconds=args.gap)
    columns = {"시간만": _scene_numbers(time_groups)}
    for threshold in THRESHOLDS:
        columns[f">{threshold}"] = _scene_numbers(split_dissimilar(time_groups, hashes, threshold))

    header = f"{'파일':32} {'촬영 시각':19} {'차이':>4} " + " ".join(f"{name:>6}" for name in columns)
    print(f"장면 번호 비교 (간격 {args.gap:g}초, '>N'은 앞 사진과 차이가 N을 넘으면 나눔)\n")
    print(header)
    for photo in photos:
        distance = distances[photo.path]
        name = str(photo.path.relative_to(folder))
        print(
            f"{name[:32]:32} {photo.datetime_original:%Y-%m-%d %H:%M:%S} "
            f"{'' if distance is None else distance:>4} "
            + " ".join(f"{scenes[photo.path]:>6}" for scenes in columns.values())
        )
    print("\n장면 수: " + ", ".join(f"{name} {max(s.values())}개" for name, s in columns.items()))


if __name__ == "__main__":
    main()
