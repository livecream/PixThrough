import cv2
import numpy as np
import pytest

from video_frame import format_duration, video_frame


def _video(path, frames=30, fps=10, size=(160, 120)):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
    if not writer.isOpened():
        pytest.skip("이 OpenCV는 mp4를 쓸 수 없음")
    for i in range(frames):
        frame = np.full((size[1], size[0], 3), 0 if i == 0 else 200, np.uint8)  # 첫 프레임만 검정
        writer.write(frame)
    writer.release()
    return path


def test_video_frame_skips_black_first_frame_and_reports_duration(tmp_path):
    path = _video(tmp_path / "clip.mp4")
    before = path.read_bytes()
    result = video_frame(path, (80, 80))
    assert result is not None
    image, info = result
    assert max(image.size) == 80
    assert image.getpixel((40, 30))[0] > 100  # 1초 지점(검정 아님)
    assert info.duration == pytest.approx(3.0, abs=0.2)
    assert (info.width, info.height) == (160, 120)
    assert path.read_bytes() == before


def test_video_frame_returns_none_for_broken_file(tmp_path):
    broken = tmp_path / "broken.mov"
    broken.write_bytes(b"not a video")
    assert video_frame(broken, (80, 80)) is None
    assert video_frame(tmp_path / "missing.mp4", (80, 80)) is None


def test_format_duration():
    assert format_duration(None) == ""
    assert format_duration(5.4) == "0:05"
    assert format_duration(125) == "2:05"
    assert format_duration(3725) == "1:02:05"
