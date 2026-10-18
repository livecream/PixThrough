"""M3: 품질 스코어링 — 선명도 + 노출 + 얼굴/눈감음.

선명도는 "초점이 맞은 부분" 기준이다. 사진 전체의 라플라시안 분산은 배경을 일부러
흐리게 찍은(아웃포커싱) 인물 사진을 초점이 정확해도 흐리다고 판정하고, 해상도가
다른 바디를 섞으면 숫자 범위 자체가 달라져 비교할 수 없었다. 그래서
  1) 긴 변을 FOCUS_LONG_SIDE로 줄인 뒤 계산하고(해상도 차이 제거),
  2) 얼굴이 검출되면 가장 선명한 얼굴 영역의 값을,
  3) 없으면 사진을 FOCUS_GRID×FOCUS_GRID 칸으로 나눠 가장 선명한 상위
     FOCUS_TOP_FRACTION 칸의 평균을 쓴다.
세 숫자는 사용자와 정한 초기값이며 test_photos 결과로 조정할 수 있다. 예전 방식
(원본 해상도·사진 전체) 값은 비교용으로 sharpness_global에 함께 남긴다.

얼굴 검출은 2단계다. FaceLandmarker에 사진을 통째로 넣으면 안쪽의 얼굴 검출기가
가까이서 찍은 큰 정면 얼굴용이라 사진을 아주 작게 줄여서 보기 때문에, 화면에서 작은
얼굴이나 옆얼굴을 거의 놓쳤다(test_photos 22장 중 1장만 검출). 그래서
  1) OpenCV YuNet으로 얼굴 위치를 먼저 찾고(FACE_DETECT_LONG_SIDES 크기마다 한 번씩),
  2) 찾은 얼굴마다 여유(FACE_CROP_MARGIN)를 두고 잘라 FaceLandmarker로 눈뜸을 잰다.
  3) 예전 방식(사진 전체를 FaceLandmarker에)으로 찾은 얼굴 중 YuNet이 놓친 것도
     합쳐서, 예전에 찾던 얼굴은 계속 찾게 한다.
잘라낸 얼굴에서 랜드마크를 못 잡으면(옆얼굴 등) 눈을 판정할 수 없는 얼굴로 보고
선명도 측정 영역으로만 쓰고 눈뜸 평균에서는 뺀다(사용자와 정한 규칙).
가장 큰 얼굴보다 많이 작은 얼굴(FACE_MAIN_MIN_RATIO)은 배경 인물로 보고 눈뜸과
선명도 계산 모두에서 뺀다(사용자와 정한 규칙). 지나가던 사람이 눈을 감았다고 C컷이
되거나, 배경 인물이 또렷하다고 초점이 맞았다고 보는 일을 막기 위해서다.

PROJECT_SPEC.md 4.3절: 선명도 → 노출 → 얼굴/눈감음 순으로 단계적으로 점수를 추가한다.
total을 여러 지표의 가중합으로 확정하는 것은 CLAUDE.md가 금지하는 "임의의 점수
가중치 확정"에 해당하므로, 사용자와 가중치를 정하기 전까지는 total = sharpness로
유지한다 (exposure/face는 참고용으로 별도 필드에 노출; face는 얼굴이 없으면 None).
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks.python import vision as mp_vision
from PIL import Image

from scanner import PhotoMetadata

# 얼굴 랜드마크 모델 (눈감음 판단용 블렌드셰이프 포함). 저장소에는 커밋하지 않고
# 최초 사용 시 다운로드해 캐시한다 (.gitignore 참고).
def _default_model_dir() -> Path:
    """모델 저장 위치. PyInstaller로 얼린 실행 파일에서는 __file__ 기준 상대경로가
    번들 내부 임시/읽기전용 경로를 가리켜 의미가 달라지므로, 그 경우 사용자 홈 아래
    쓰기 가능한 경로를 대신 쓴다."""
    if getattr(sys, "frozen", False):
        return Path.home() / "Library" / "Application Support" / "PixThrough" / "models"
    return Path(__file__).resolve().parent.parent / "models"


_MODEL_DIR = _default_model_dir()
_MODEL_PATH = _MODEL_DIR / "face_landmarker.task"
_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/1/face_landmarker.task"
)

_YUNET_PATH = _MODEL_DIR / "face_detection_yunet_2023mar.onnx"
_YUNET_URL = (
    "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/"
    "face_detection_yunet/face_detection_yunet_2023mar.onnx"
)
_YUNET_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"

_landmarker: mp_vision.FaceLandmarker | None = None
_crop_landmarker: mp_vision.FaceLandmarker | None = None
_yunet: cv2.FaceDetectorYN | None = None

FOCUS_LONG_SIDE = 2000
FOCUS_GRID = 8
FOCUS_TOP_FRACTION = 0.1
# 줄인 이미지 기준 이보다 작은 얼굴은 픽셀이 너무 적어 선명도 값이 들쭉날쭉하므로
# 얼굴 기준 측정에서 뺀다(눈뜸 점수에는 그대로 쓴다).
MIN_FACE_SIDE_PX = 32

# 얼굴 검출 초기값(사용자와 정한 값, test_photos 결과로 조정 가능).
# YuNet 신뢰도가 이보다 낮은 후보는 얼굴로 보지 않는다.
FACE_DETECT_SCORE = 0.6
# 긴 변을 이 크기들로 줄여 각각 검출한다. 큰 쪽은 작은 얼굴을, 작은 쪽은 화면을
# 가득 채운 큰 얼굴을 잡는다.
FACE_DETECT_LONG_SIDES = (1600, 640)
# 랜드마크용으로 얼굴을 자를 때 상자 크기 대비 사방에 더하는 여유 비율.
FACE_CROP_MARGIN = 0.4
# 두 검출 결과의 상자가 이만큼(IoU) 겹치면 같은 얼굴로 본다.
FACE_DEDUP_IOU = 0.3
# 가장 큰 얼굴의 긴 변 대비 이 비율보다 작은 얼굴은 배경 인물로 보고 눈뜸·선명도
# 계산에서 뺀다(사용자와 정한 규칙: 배경 얼굴 제외. 비율은 초기값).
FACE_MAIN_MIN_RATIO = 0.5

FACE_SOURCE_YUNET = "yunet"
FACE_SOURCE_LANDMARKER = "landmarker"

SHARPNESS_REGION_FACE = "face"
SHARPNESS_REGION_TILES = "tiles"


@dataclass(frozen=True)
class PhotoScore:
    path: Path
    sharpness: float
    exposure: float
    face: float | None
    total: float
    error: str | None = None
    sharpness_global: float | None = None  # 예전 방식(원본 해상도·사진 전체), 비교용
    sharpness_region: str | None = None  # SHARPNESS_REGION_FACE / _TILES


@dataclass(frozen=True)
class _DetectedFace:
    # 눈 뜬 정도(0~1). 옆얼굴 등 랜드마크를 못 잡아 판정할 수 없으면 None.
    eyes_open: float | None
    # 정규화 좌표(0~1) 경계 상자: (x0, y0, x1, y1)
    bbox: tuple[float, float, float, float]
    source: str = FACE_SOURCE_LANDMARKER
    confidence: float | None = None  # YuNet 신뢰도(예전 방식으로 찾은 얼굴은 None)


def _decode(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """이미지를 한 번만 디스크에서 읽어 (grayscale, rgb) 배열 쌍으로 반환한다.

    선명도/노출/얼굴 세 지표를 각자 따로 파일을 열면 실사진(수 MB, 수천만 화소) 기준
    사진당 처리 시간이 3배로 늘어나는 게 실측으로 확인돼(장당 ~0.33s), score_photo가
    이 함수로 한 번만 디코딩해 공유한다.
    """
    with Image.open(path) as img:
        rgb_img = img.convert("RGB")
        gray = np.array(rgb_img.convert("L"))
        rgb = np.array(rgb_img)
    return gray, rgb


def _sharpness_from_gray(gray: np.ndarray) -> float:
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _downscale(gray: np.ndarray, long_side: int = FOCUS_LONG_SIDE) -> np.ndarray:
    """긴 변이 long_side보다 크면 비율 유지한 채 줄인다(작으면 그대로, 키우지 않음)."""
    return _resize_long_side(gray, long_side)


def _resize_long_side(img: np.ndarray, long_side: int) -> np.ndarray:
    """긴 변이 long_side보다 크면 비율 유지한 채 줄인다(작으면 그대로)."""
    h, w = img.shape[:2]
    if max(h, w) <= long_side:
        return img
    scale = long_side / max(h, w)
    size = (max(1, round(w * scale)), max(1, round(h * scale)))
    return cv2.resize(img, size, interpolation=cv2.INTER_AREA)


def _tile_sharpness(laplacian: np.ndarray) -> float:
    """라플라시안 결과를 FOCUS_GRID×FOCUS_GRID 칸으로 나눠, 분산이 가장 큰 상위
    FOCUS_TOP_FRACTION 칸의 평균을 낸다 — 초점이 맞은 부분만의 선명도."""
    h, w = laplacian.shape
    rows = min(FOCUS_GRID, h)
    cols = min(FOCUS_GRID, w)
    ys = np.linspace(0, h, rows + 1, dtype=int)
    xs = np.linspace(0, w, cols + 1, dtype=int)
    tile_vars = sorted(
        (
            float(laplacian[ys[r] : ys[r + 1], xs[c] : xs[c + 1]].var())
            for r in range(rows)
            for c in range(cols)
        ),
        reverse=True,
    )
    top_n = max(1, round(len(tile_vars) * FOCUS_TOP_FRACTION))
    return sum(tile_vars[:top_n]) / top_n


def _face_sharpness(laplacian: np.ndarray, faces: list[_DetectedFace]) -> float | None:
    """검출된 얼굴 중 가장 선명한 얼굴 영역의 라플라시안 분산. 쓸 만한 크기의
    얼굴이 없으면 None."""
    h, w = laplacian.shape
    values = []
    for face in faces:
        x0, y0, x1, y1 = face.bbox
        left, right = max(0, int(x0 * w)), min(w, int(np.ceil(x1 * w)))
        top, bottom = max(0, int(y0 * h)), min(h, int(np.ceil(y1 * h)))
        if right - left < MIN_FACE_SIDE_PX or bottom - top < MIN_FACE_SIDE_PX:
            continue
        values.append(float(laplacian[top:bottom, left:right].var()))
    return max(values) if values else None


def _focus_sharpness(gray: np.ndarray, faces: list[_DetectedFace]) -> tuple[float, str]:
    """초점 영역 기준 선명도와, 어디서 쟀는지(얼굴/칸)를 함께 반환한다."""
    laplacian = cv2.Laplacian(_downscale(gray), cv2.CV_64F)
    face_value = _face_sharpness(laplacian, faces)
    if face_value is not None:
        return face_value, SHARPNESS_REGION_FACE
    return _tile_sharpness(laplacian), SHARPNESS_REGION_TILES


def _exposure_from_gray(gray: np.ndarray) -> float:
    clipped = np.count_nonzero(gray == 0) + np.count_nonzero(gray == 255)
    return 1.0 - clipped / gray.size


def sharpness_score(path: Path) -> float:
    """초점 영역 기준 라플라시안 분산 선명도 점수. 높을수록 선명(블러 적음)."""
    gray, rgb = _decode(path)
    return _focus_sharpness(gray, _detect_faces(rgb))[0]


def exposure_score(path: Path) -> float:
    """히스토그램 클리핑 기반 노출 적정성 점수.

    순수 검정(0)·순수 흰색(255)으로 잘린 픽셀 비율이 높을수록 노출 손실이 큰 것으로
    보고 점수를 낮춘다. 1.0(클리핑 없음) ~ 0.0(전부 클리핑) 범위.
    """
    gray, _ = _decode(path)
    return _exposure_from_gray(gray)


def _download(url: str, path: Path, sha256: str | None = None) -> Path:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        urllib.request.urlretrieve(url, tmp)
        if sha256 is not None:
            actual = hashlib.sha256(tmp.read_bytes()).hexdigest()
            if actual != sha256:
                tmp.unlink()
                raise RuntimeError(f"모델 파일이 손상됐거나 다른 파일임: {path.name}")
        tmp.replace(path)
    return path


def _ensure_model() -> Path:
    return _download(_MODEL_URL, _MODEL_PATH)


def _ensure_yunet_model() -> Path:
    return _download(_YUNET_URL, _YUNET_PATH, _YUNET_SHA256)


def _create_landmarker(num_faces: int) -> mp_vision.FaceLandmarker:
    base_options = mp.tasks.BaseOptions(model_asset_path=str(_ensure_model()))
    options = mp_vision.FaceLandmarkerOptions(
        base_options=base_options, output_face_blendshapes=True, num_faces=num_faces
    )
    return mp_vision.FaceLandmarker.create_from_options(options)


def _get_landmarker() -> mp_vision.FaceLandmarker:
    """사진 전체에서 얼굴을 찾는 예전 방식용."""
    global _landmarker
    if _landmarker is None:
        _landmarker = _create_landmarker(num_faces=5)
    return _landmarker


def _get_crop_landmarker() -> mp_vision.FaceLandmarker:
    """YuNet이 찾은 얼굴 하나를 잘라낸 이미지용."""
    global _crop_landmarker
    if _crop_landmarker is None:
        _crop_landmarker = _create_landmarker(num_faces=1)
    return _crop_landmarker


def _get_yunet() -> cv2.FaceDetectorYN:
    global _yunet
    if _yunet is None:
        _yunet = cv2.FaceDetectorYN.create(
            str(_ensure_yunet_model()), "", (320, 320), FACE_DETECT_SCORE
        )
    return _yunet


def _eyes_open_from_scores(blink_left: float, blink_right: float) -> float:
    """블렌드셰이프의 eyeBlinkLeft/Right 점수(높을수록 감김)를 눈 뜬 정도로 뒤집는다.

    한쪽 눈만 감아도(윙크 등) 눈감음으로 간주하도록 더 심하게 감긴 쪽을 기준으로 삼는다.
    """
    return 1.0 - max(blink_left, blink_right)


def _eyes_open_from_blendshapes(blendshapes) -> float:
    scores = {c.category_name: c.score for c in blendshapes}
    return _eyes_open_from_scores(
        scores.get("eyeBlinkLeft", 0.0), scores.get("eyeBlinkRight", 0.0)
    )


def _iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _merge_faces(
    primary: list[_DetectedFace], extra: list[_DetectedFace]
) -> list[_DetectedFace]:
    """primary를 신뢰도 높은 순으로 겹침 제거한 뒤, extra 중 primary와 겹치지 않는
    얼굴만 덧붙인다."""
    merged: list[_DetectedFace] = []
    ordered = sorted(primary, key=lambda f: f.confidence or 0.0, reverse=True)
    for face in [*ordered, *extra]:
        if all(_iou(face.bbox, kept.bbox) < FACE_DEDUP_IOU for kept in merged):
            merged.append(face)
    return merged


def _yunet_boxes(rgb: np.ndarray) -> list[tuple[tuple[float, float, float, float], float]]:
    """YuNet으로 찾은 얼굴 상자(정규화 좌표)와 신뢰도. 크기별 결과를 겹침 제거해 합친다."""
    h, w = rgb.shape[:2]
    detector = _get_yunet()
    found: list[_DetectedFace] = []
    # 원본(수천만 화소)은 한 번만 줄이고, 작은 크기는 줄인 것에서 다시 줄인다.
    bgr = cv2.cvtColor(_resize_long_side(rgb, max(FACE_DETECT_LONG_SIDES)), cv2.COLOR_RGB2BGR)
    for long_side in sorted(FACE_DETECT_LONG_SIDES, reverse=True):
        img = _resize_long_side(bgr, long_side)
        size = (img.shape[1], img.shape[0])
        detector.setInputSize(size)
        _, detections = detector.detect(img)
        if detections is None:
            continue
        sw, sh = size
        for d in detections:
            x, y, bw, bh, score = float(d[0]), float(d[1]), float(d[2]), float(d[3]), float(d[-1])
            bbox = (
                max(0.0, x / sw),
                max(0.0, y / sh),
                min(1.0, (x + bw) / sw),
                min(1.0, (y + bh) / sh),
            )
            if bbox[2] > bbox[0] and bbox[3] > bbox[1]:
                found.append(_DetectedFace(eyes_open=None, bbox=bbox, confidence=score))
    return [(f.bbox, f.confidence) for f in _merge_faces(found, [])]


def _eyes_open_in_crop(
    rgb: np.ndarray, bbox: tuple[float, float, float, float]
) -> float | None:
    """얼굴 상자 주변을 여유 있게 잘라 FaceLandmarker로 눈 뜬 정도를 잰다.
    랜드마크를 못 잡으면(옆얼굴 등) None."""
    h, w = rgb.shape[:2]
    x0, y0, x1, y1 = bbox
    mx = (x1 - x0) * FACE_CROP_MARGIN
    my = (y1 - y0) * FACE_CROP_MARGIN
    left, right = max(0, int((x0 - mx) * w)), min(w, int(np.ceil((x1 + mx) * w)))
    top, bottom = max(0, int((y0 - my) * h)), min(h, int(np.ceil((y1 + my) * h)))
    if right - left < 2 or bottom - top < 2:
        return None
    # 랜드마크 모델은 어차피 작은 입력으로 줄여서 보므로 미리 줄여 시간을 아낀다.
    crop = np.ascontiguousarray(_resize_long_side(rgb[top:bottom, left:right], 512))
    result = _get_crop_landmarker().detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=crop))
    if not result.face_blendshapes:
        return None
    return _eyes_open_from_blendshapes(result.face_blendshapes[0])


def _detect_faces_whole_image(rgb: np.ndarray) -> list[_DetectedFace]:
    """예전 방식: 사진 전체를 FaceLandmarker에 넣는다."""
    image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    result = _get_landmarker().detect(image)
    faces = []
    for blendshapes, landmarks in zip(result.face_blendshapes, result.face_landmarks):
        xs = [lm.x for lm in landmarks]
        ys = [lm.y for lm in landmarks]
        faces.append(
            _DetectedFace(
                eyes_open=_eyes_open_from_blendshapes(blendshapes),
                bbox=(min(xs), min(ys), max(xs), max(ys)),
                source=FACE_SOURCE_LANDMARKER,
            )
        )
    return faces


def _face_side_px(face: _DetectedFace, width: int, height: int) -> float:
    x0, y0, x1, y1 = face.bbox
    return max((x1 - x0) * width, (y1 - y0) * height)


def _is_main_face(face: _DetectedFace, faces: list[_DetectedFace], width: int, height: int) -> bool:
    """가장 큰 얼굴 대비 FACE_MAIN_MIN_RATIO 이상이면 주인공 얼굴, 아니면 배경 얼굴."""
    largest = max(_face_side_px(f, width, height) for f in faces)
    return _face_side_px(face, width, height) >= largest * FACE_MAIN_MIN_RATIO


def _detect_faces(rgb: np.ndarray) -> list[_DetectedFace]:
    """주인공 얼굴(배경 얼굴 제외)마다 눈 뜬 정도와 경계 상자를 구한다. 경계 상자는
    선명도를 얼굴 영역에서 재는 데 쓴다. 2단계 검출 방식은 모듈 설명 참고."""
    h, w = rgb.shape[:2]
    faces = _detect_all_faces(rgb)
    return [f for f in faces if _is_main_face(f, faces, w, h)]


def _detect_all_faces(rgb: np.ndarray) -> list[_DetectedFace]:
    """배경 얼굴까지 포함한 검출 결과."""
    rgb = np.ascontiguousarray(rgb)
    yunet_faces = [
        _DetectedFace(
            eyes_open=_eyes_open_in_crop(rgb, bbox),
            bbox=bbox,
            source=FACE_SOURCE_YUNET,
            confidence=confidence,
        )
        for bbox, confidence in _yunet_boxes(rgb)
    ]
    return _merge_faces(yunet_faces, _detect_faces_whole_image(rgb))


def _eyes_open_of(faces: list[_DetectedFace]) -> float | None:
    """눈을 판정할 수 있는 얼굴들의 눈뜸 평균. 그런 얼굴이 없으면 None."""
    values = [f.eyes_open for f in faces if f.eyes_open is not None]
    if not values:
        return None
    return sum(values) / len(values)


@dataclass(frozen=True)
class FaceDiagnosis:
    """`cli.py faces` 진단용: 새 방식과 예전 방식의 검출 결과를 나란히 담는다."""
    path: Path
    width: int
    height: int
    faces: list[_DetectedFace]  # 배경 얼굴 포함
    legacy_faces: list[_DetectedFace]
    error: str | None = None

    def is_main(self, face: _DetectedFace) -> bool:
        return _is_main_face(face, self.faces, self.width, self.height)


def diagnose_faces(path: Path) -> FaceDiagnosis:
    try:
        _, rgb = _decode(path)
        rgb = np.ascontiguousarray(rgb)
        legacy = _detect_faces_whole_image(rgb)
        faces = _detect_all_faces(rgb)
    except Exception as e:
        return FaceDiagnosis(path, 0, 0, [], [], error=f"얼굴 검출 실패: {e}")
    h, w = rgb.shape[:2]
    return FaceDiagnosis(path, w, h, faces, legacy)


def face_score(path: Path) -> float | None:
    """얼굴이 있으면 눈 뜬 정도(0~1, 높을수록 좋음)를, 없으면 None(해당 없음)을 반환한다.

    PROJECT_SPEC.md 4.3절: 인물 사진에서만 의미 있는 기준이라 풍경 사진 등 얼굴이
    없는 경우는 페널티 없이 "적용 대상 아님"으로 취급한다.
    """
    _, rgb = _decode(path)
    return _eyes_open_of(_detect_faces(rgb))


def score_photo(meta: PhotoMetadata) -> PhotoScore:
    """단일 사진의 품질 점수를 계산한다.

    스캔 단계에서 이미 오류가 기록된 파일(예: 손상된 이미지)은 다시 열어보지 않고
    그 오류를 그대로 전달한다.
    """
    if meta.error is not None:
        return PhotoScore(
            path=meta.path,
            sharpness=0.0,
            exposure=0.0,
            face=None,
            total=0.0,
            error=meta.error,
        )

    try:
        gray, rgb = _decode(meta.path)
        faces = _detect_faces(rgb)
        sharpness, sharpness_region = _focus_sharpness(gray, faces)
        sharpness_global = _sharpness_from_gray(gray)
        exposure = _exposure_from_gray(gray)
        face = _eyes_open_of(faces)
    except Exception as e:
        return PhotoScore(
            path=meta.path,
            sharpness=0.0,
            exposure=0.0,
            face=None,
            total=0.0,
            error=f"스코어링 실패: {e}",
        )

    return PhotoScore(
        path=meta.path,
        sharpness=sharpness,
        exposure=exposure,
        face=face,
        total=sharpness,
        sharpness_global=sharpness_global,
        sharpness_region=sharpness_region,
    )


def _stat_key(path: Path) -> tuple[float, int]:
    stat = path.stat()
    return stat.st_mtime, stat.st_size


def _score_photo_cached(
    meta: PhotoMetadata, cache: dict[Path, tuple[float, int, PhotoScore]] | None
) -> PhotoScore:
    """cache가 주어지면 파일의 (수정 시각, 크기)가 이전과 같을 때 다시 채점하지
    않고 캐시된 점수를 재사용한다. 얼굴 검출(YuNet+mediapipe)이 사진 1장당 수백 ms대라
    같은 세션에서 반복 스코어링할 때 특히 효과가 크다."""
    if cache is None:
        return score_photo(meta)
    key = _stat_key(meta.path)
    cached = cache.get(meta.path)
    if cached is not None and cached[:2] == key:
        return cached[2]
    score = score_photo(meta)
    cache[meta.path] = (*key, score)
    return score


def score_photos(
    photos: list[PhotoMetadata],
    on_progress: Callable[[int, int], None] | None = None,
    cache: dict[Path, tuple[float, int, PhotoScore]] | None = None,
) -> list[PhotoScore]:
    """사진 목록 각각의 품질 점수를 계산한다.

    on_progress가 주어지면 사진 1장 처리할 때마다 (완료한 수, 전체 수)를 알려준다.
    """
    total = len(photos)
    results: list[PhotoScore] = []
    for i, m in enumerate(photos, start=1):
        results.append(_score_photo_cached(m, cache))
        if on_progress is not None:
            on_progress(i, total)
    return results
