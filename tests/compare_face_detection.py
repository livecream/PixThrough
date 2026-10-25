"""얼굴 검출 방식 비교: 예전(mediapipe FaceLandmarker 단독) vs 지금(YuNet + 잘라서 눈 분석).

얼굴 검출은 사진에 따라 결과가 크게 달라, 합성 이미지 테스트만으로는 개선됐는지 알 수
없다. test_photos/(또는 지정한 폴더)의 실사진으로 사진마다 몇 개의 얼굴을 찾았는지,
눈 분석까지 된 얼굴은 몇 개인지를 나란히 보여준다. 결과를 사람이 보고 판단하는 용도라
pytest 테스트로는 돌지 않는다(파일 이름이 test_로 시작하지 않음).

    python tests/compare_face_detection.py            # test_photos/
    python tests/compare_face_detection.py 폴더경로
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import mediapipe as mp  # noqa: E402
import numpy as np  # noqa: E402

import scoring  # noqa: E402
from scanner import scan_and_extract  # noqa: E402

TEST_PHOTOS_DIR = Path(__file__).resolve().parent.parent / "test_photos"


def _old_face_count(rgb: np.ndarray) -> int:
    """예전 방식: 사진 전체를 FaceLandmarker에 바로 넣었을 때 찾은 얼굴 수."""
    image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
    return len(scoring._get_landmarker().detect(image).face_blendshapes)


def main() -> None:
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else TEST_PHOTOS_DIR
    photos = [p for p in scan_and_extract(folder, recursive=True) if p.error is None]
    if not photos:
        print(f"사진이 없습니다: {folder}")
        return

    print(f"{'파일':40} {'예전':>4} {'지금':>4} {'눈분석':>6} {'눈뜸':>5} {'선명도 기준':>10} {'시간':>6}")
    totals = [0, 0, 0]
    photos_with_face = [0, 0]
    for meta in photos:
        gray, rgb = scoring._decode(meta.path)
        old = _old_face_count(rgb)
        started = time.perf_counter()
        faces = scoring._detect_faces(rgb)
        elapsed = time.perf_counter() - started
        analysed = sum(1 for f in faces if f.eyes_open is not None)
        eyes = scoring._eyes_open_of(faces)
        _, region = scoring._focus_sharpness(gray, faces)

        totals = [totals[0] + old, totals[1] + len(faces), totals[2] + analysed]
        photos_with_face[0] += old > 0
        photos_with_face[1] += eyes is not None
        name = str(meta.path.relative_to(folder))
        print(
            f"{name[:40]:40} {old:>4} {len(faces):>4} {analysed:>6} "
            f"{'' if eyes is None else f'{eyes:.2f}':>5} {region:>10} {elapsed:>5.2f}s"
        )

    print(
        f"\n합계: 예전 {totals[0]}개 / 지금 {totals[1]}개(눈 분석 {totals[2]}개)\n"
        f"눈뜸 점수가 있는 사진: 예전 {photos_with_face[0]}장 → 지금 {photos_with_face[1]}장 "
        f"(전체 {len(photos)}장)"
    )


if __name__ == "__main__":
    main()
