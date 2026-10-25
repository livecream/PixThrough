import sys

import cv2
import numpy as np
import pytest
from pathlib import Path
from PIL import Image, ImageFilter

import scoring
from scanner import PhotoMetadata
from scoring import (
    _default_model_dir,
    _eyes_open_from_scores,
    exposure_score,
    face_score,
    score_photo,
    score_photos,
    sharpness_score,
)


def _make_checkerboard(path, size=64, block=4):
    arr = (np.indices((size, size)).sum(axis=0) // block % 2 * 255).astype("uint8")
    Image.fromarray(arr).convert("RGB").save(path, "png")
    return path


def _make_flat(path, size=64, color=128):
    Image.new("RGB", (size, size), (color, color, color)).save(path, "png")
    return path


def _make_blurred_checkerboard(path, size=64, block=4):
    arr = (np.indices((size, size)).sum(axis=0) // block % 2 * 255).astype("uint8")
    img = Image.fromarray(arr).convert("RGB").filter(ImageFilter.GaussianBlur(radius=5))
    img.save(path, "png")
    return path


def test_exposure_score_is_perfect_for_mid_gray_image(tmp_path):
    path = _make_flat(tmp_path / "mid.png", color=128)

    assert exposure_score(path) == 1.0


def test_exposure_score_is_zero_for_pure_black_image(tmp_path):
    path = _make_flat(tmp_path / "black.png", color=0)

    assert exposure_score(path) == 0.0


def test_exposure_score_is_zero_for_pure_white_image(tmp_path):
    path = _make_flat(tmp_path / "white.png", color=255)

    assert exposure_score(path) == 0.0


def test_exposure_score_reflects_partial_clipping(tmp_path):
    size = 64
    arr = np.zeros((size, size), dtype="uint8")
    arr[: size // 2] = 0  # 절반 완전 검정(클리핑)
    arr[size // 2 :] = 128  # 절반 중간 밝기(클리핑 아님)
    path = tmp_path / "half_clipped.png"
    Image.fromarray(arr).convert("RGB").save(path, "png")

    assert exposure_score(path) == pytest.approx(0.5)


def test_eyes_open_from_scores_fully_open_when_no_blink():
    assert _eyes_open_from_scores(blink_left=0.0, blink_right=0.0) == 1.0


def test_eyes_open_from_scores_fully_closed_when_either_eye_blinks():
    assert _eyes_open_from_scores(blink_left=1.0, blink_right=0.0) == 0.0
    assert _eyes_open_from_scores(blink_left=0.0, blink_right=1.0) == 0.0


def test_eyes_open_from_scores_takes_worst_eye():
    assert _eyes_open_from_scores(blink_left=0.3, blink_right=0.7) == pytest.approx(0.3)


def test_face_score_is_none_when_no_face_detected(tmp_path):
    path = _make_flat(tmp_path / "no_face.png")

    assert face_score(path) is None


def test_sharpness_score_is_zero_for_flat_image(tmp_path):
    path = _make_flat(tmp_path / "flat.png")

    assert sharpness_score(path) == 0.0


def test_sharpness_score_higher_for_sharp_than_blurred(tmp_path):
    sharp = _make_checkerboard(tmp_path / "sharp.png")
    blurred = _make_blurred_checkerboard(tmp_path / "blurred.png")

    assert sharpness_score(sharp) > sharpness_score(blurred)


def test_score_photo_uses_sharpness_as_total(tmp_path):
    path = _make_checkerboard(tmp_path / "sharp.png")
    meta = PhotoMetadata(path=path, datetime_original=None, exif_source="exif")

    result = score_photo(meta)

    assert result.path == path
    assert result.sharpness > 0
    assert result.total == result.sharpness
    assert result.error is None


def test_score_photo_propagates_existing_metadata_error(tmp_path):
    path = tmp_path / "broken.png"
    path.write_bytes(b"not a real image")
    meta = PhotoMetadata(
        path=path,
        datetime_original=None,
        exif_source="mtime",
        error="이미지를 열 수 없음",
    )

    result = score_photo(meta)

    assert result.total == 0.0
    assert result.error == "이미지를 열 수 없음"


def test_score_photo_reports_error_when_scoring_itself_fails(tmp_path):
    path = tmp_path / "broken.png"
    path.write_bytes(b"not a real image")
    meta = PhotoMetadata(path=path, datetime_original=None, exif_source="mtime")

    result = score_photo(meta)

    assert result.total == 0.0
    assert result.error is not None


def test_score_photos_scores_each_photo(tmp_path):
    p1 = _make_checkerboard(tmp_path / "a.png")
    p2 = _make_flat(tmp_path / "b.png")
    metas = [
        PhotoMetadata(path=p1, datetime_original=None, exif_source="exif"),
        PhotoMetadata(path=p2, datetime_original=None, exif_source="exif"),
    ]

    results = score_photos(metas)

    assert [r.path for r in results] == [p1, p2]


def test_score_photos_reports_progress_for_each_photo(tmp_path):
    p1 = _make_checkerboard(tmp_path / "a.png")
    p2 = _make_flat(tmp_path / "b.png")
    metas = [
        PhotoMetadata(path=p1, datetime_original=None, exif_source="exif"),
        PhotoMetadata(path=p2, datetime_original=None, exif_source="exif"),
    ]
    calls = []

    score_photos(metas, on_progress=lambda i, total: calls.append((i, total)))

    assert calls == [(1, 2), (2, 2)]


def test_score_photos_works_without_progress_callback(tmp_path):
    p1 = _make_flat(tmp_path / "a.png")
    metas = [PhotoMetadata(path=p1, datetime_original=None, exif_source="exif")]

    results = score_photos(metas)

    assert len(results) == 1


def test_default_model_dir_uses_app_support_when_frozen_on_macos(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")

    result = _default_model_dir()

    assert result == Path.home() / "Library" / "Application Support" / "PixThrough" / "models"


def test_default_model_dir_uses_local_app_data_when_frozen_on_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert _default_model_dir() == tmp_path / "PixThrough" / "models"


def test_default_model_dir_falls_back_without_local_app_data_on_windows(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    assert _default_model_dir() == Path.home() / "AppData" / "Local" / "PixThrough" / "models"


def test_default_model_dir_uses_repo_relative_path_when_not_frozen(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)

    result = _default_model_dir()

    assert result == Path(scoring.__file__).resolve().parent.parent / "models"


# --- 초점 영역 기준 선명도 ---------------------------------------------------


def _texture(h, w, seed=0):
    """실사진처럼 굵은 형태부터 잔디테일까지 고루 섞인(1/f 스펙트럼) 회색 텍스처."""
    rng = np.random.default_rng(seed)
    fy = np.fft.fftfreq(h)[:, None]
    fx = np.fft.rfftfreq(w)[None, :]
    f = np.sqrt(fx**2 + fy**2)
    f[0, 0] = 1
    spectrum = (rng.normal(size=f.shape) + 1j * rng.normal(size=f.shape)) / f
    img = np.fft.irfft2(spectrum, s=(h, w))
    return ((img - img.min()) / (img.max() - img.min()) * 255).astype("uint8")


def _blur(arr, sigma):
    return cv2.GaussianBlur(arr, (0, 0), sigma)


def _save_gray(path, arr):
    Image.fromarray(arr).convert("RGB").save(path, "png")
    return path


def test_downscale_shrinks_long_side_and_never_upscales():
    big = np.zeros((3000, 6000), dtype="uint8")
    small = np.zeros((100, 200), dtype="uint8")

    assert scoring._downscale(big, long_side=2000).shape == (1000, 2000)
    assert scoring._downscale(small, long_side=2000).shape == (100, 200)


def test_focus_sharpness_is_not_penalised_by_blurred_background(tmp_path, monkeypatch):
    """아웃포커싱 사진(피사체만 또렷, 배경은 크게 흐림)도 피사체가 또렷하면 전부
    또렷한 사진과 비슷하게 선명해야 한다. 예전 방식(사진 전체)은 크게 깎았다."""
    monkeypatch.setattr(scoring, "_get_landmarker", _no_face_landmarker)
    base = _texture(400, 400)
    bokeh = _blur(base, 8)
    bokeh[100:300, 100:300] = base[100:300, 100:300]
    bokeh_path = _save_gray(tmp_path / "bokeh.png", bokeh)
    sharp_path = _save_gray(tmp_path / "sharp.png", base)

    bokeh_score = score_photo(PhotoMetadata(path=bokeh_path, datetime_original=None, exif_source="exif"))
    sharp_score = score_photo(PhotoMetadata(path=sharp_path, datetime_original=None, exif_source="exif"))

    assert bokeh_score.sharpness / sharp_score.sharpness > 0.8
    assert bokeh_score.sharpness_global / sharp_score.sharpness_global < 0.5
    assert bokeh_score.sharpness_region == scoring.SHARPNESS_REGION_TILES


def test_focus_sharpness_still_flags_whole_image_blur(monkeypatch):
    base = _texture(400, 400)

    sharp, _ = scoring._focus_sharpness(base, [])
    shaken, _ = scoring._focus_sharpness(_blur(base, 1.5), [])

    assert shaken < sharp * 0.2


def test_focus_sharpness_narrows_gap_across_resolutions():
    """같은 장면을 화소가 다른 두 바디(6000px/3000px)로 찍은 경우. 예전 방식은 몇 배씩
    차이 났지만, 줄여서 재므로 차이가 크게 줄어야 한다."""
    high_res = _blur(_texture(4000, 6000, seed=1), 1.0)
    low_res = cv2.resize(high_res, (3000, 2000), interpolation=cv2.INTER_AREA)

    high, _ = scoring._focus_sharpness(high_res, [])
    low, _ = scoring._focus_sharpness(low_res, [])
    old_high = scoring._sharpness_from_gray(high_res)
    old_low = scoring._sharpness_from_gray(low_res)

    new_ratio = max(high, low) / min(high, low)
    old_ratio = max(old_high, old_low) / min(old_high, old_low)
    assert new_ratio < 1.5
    assert old_ratio > 3


def test_focus_sharpness_uses_sharpest_face_region():
    gray = np.full((400, 400), 128, dtype="uint8")
    gray[0:100, 0:100] = _texture(100, 100)  # 왼쪽 위: 또렷한 얼굴
    gray[300:400, 300:400] = np.array(
        Image.fromarray(_texture(100, 100, seed=1)).filter(ImageFilter.GaussianBlur(radius=3))
    )  # 오른쪽 아래: 흐린 얼굴
    sharp_face = scoring._DetectedFace(eyes_open=1.0, bbox=(0.0, 0.0, 0.25, 0.25))
    blurred_face = scoring._DetectedFace(eyes_open=1.0, bbox=(0.75, 0.75, 1.0, 1.0))

    value, region = scoring._focus_sharpness(gray, [blurred_face, sharp_face])
    blurred_only, _ = scoring._focus_sharpness(gray, [blurred_face])

    assert region == scoring.SHARPNESS_REGION_FACE
    assert value > blurred_only


def test_focus_sharpness_ignores_tiny_faces_and_falls_back_to_tiles():
    gray = _texture(400, 400)
    tiny_face = scoring._DetectedFace(eyes_open=1.0, bbox=(0.5, 0.5, 0.52, 0.52))  # 8px

    _, region = scoring._focus_sharpness(gray, [tiny_face])

    assert region == scoring.SHARPNESS_REGION_TILES


def test_score_photo_keeps_eyes_open_average_and_global_sharpness(tmp_path, monkeypatch):
    path = _save_gray(tmp_path / "faces.png", _texture(200, 200))
    faces = [
        scoring._DetectedFace(eyes_open=1.0, bbox=(0.0, 0.0, 0.5, 0.5)),
        scoring._DetectedFace(eyes_open=0.5, bbox=(0.5, 0.5, 1.0, 1.0)),
    ]
    monkeypatch.setattr(scoring, "_detect_faces", lambda rgb: faces)

    result = score_photo(PhotoMetadata(path=path, datetime_original=None, exif_source="exif"))

    assert result.face == pytest.approx(0.75)
    assert result.sharpness_region == scoring.SHARPNESS_REGION_FACE
    assert result.sharpness_global == pytest.approx(scoring._sharpness_from_gray(_texture(200, 200)))
    assert result.total == result.sharpness


class _NoFaceResult:
    face_blendshapes: list = []
    face_landmarks: list = []


class _NoFaceDetector:
    def detect(self, image):
        return _NoFaceResult()


def _no_face_landmarker():
    return _NoFaceDetector()
