"""영상 파일의 한 장면과 길이를 읽는다(표 옆 미리보기 칸용). 영상은 읽기만 한다.

첫 프레임은 검은 화면(페이드 인)인 경우가 많아 영상 길이의 10%(최대 1초) 지점을 쓰고, 그 자리를
못 읽으면 첫 프레임으로 돌아간다. OpenCV에 들어 있는 FFmpeg로 읽어서 코덱에 따라(예: 일부
HEVC·ProRes) 못 읽을 수 있다 — 그때는 None.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
from PIL import Image

SEEK_FRACTION = 0.1
SEEK_MAX_SECONDS = 1.0


@dataclass(frozen=True)
class VideoInfo:
    duration: float | None  # 초, 모르면 None
    width: int
    height: int


def _open(path: Path) -> cv2.VideoCapture | None:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        capture.release()
        return None
    return capture


def _info(capture: cv2.VideoCapture) -> VideoInfo:
    fps = capture.get(cv2.CAP_PROP_FPS) or 0.0
    frames = capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0
    duration = frames / fps if fps > 0 and frames > 0 else None
    return VideoInfo(duration, int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)))


def video_frame(path: Path, size: tuple[int, int]) -> tuple[Image.Image, VideoInfo] | None:
    """영상의 한 장면을 size 안에 들어가게 줄인 그림과 영상 정보. 못 읽으면 None."""
    try:
        capture = _open(Path(path))
    except Exception:  # noqa: BLE001 - 깨진 영상은 "열 수 없음"으로
        return None
    if capture is None:
        return None
    try:
        info = _info(capture)
        frame = None
        if info.duration:
            capture.set(cv2.CAP_PROP_POS_MSEC, min(info.duration * SEEK_FRACTION, SEEK_MAX_SECONDS) * 1000)
            ok, frame = capture.read()
            if not ok:
                frame = None
                capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
        if frame is None:
            ok, frame = capture.read()
            if not ok or frame is None:
                return None
        image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        image.thumbnail(size, Image.Resampling.LANCZOS)
        return image, info
    except Exception:  # noqa: BLE001
        return None
    finally:
        capture.release()


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return ""
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"
