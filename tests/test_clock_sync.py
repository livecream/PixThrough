import datetime
from pathlib import Path

import pytest

from clock_sync import (
    camera_key,
    camera_keys,
    format_offset,
    offset_from_pair,
    parse_offset,
    shift_photos,
    summarize,
)
from scanner import PhotoMetadata

T0 = datetime.datetime(2026, 5, 1, 14, 0, 0)


def _meta(path, seconds, make="Canon", model="Canon EOS R5", serial=None):
    return PhotoMetadata(path=Path(path), datetime_original=T0 + datetime.timedelta(seconds=seconds),
                         exif_source="exif", camera_make=make, camera_model=model, camera_serial=serial)


def test_camera_key_names_body_once_and_adds_serial():
    assert camera_key(_meta("a.jpg", 0)) == "Canon EOS R5"
    assert camera_key(_meta("a.jpg", 0, serial="123")) == "Canon EOS R5 #123"
    assert camera_key(_meta("a.jpg", 0, make="SONY", model="ILCE-7M4")) == "SONY ILCE-7M4"
    assert camera_key(_meta("a.jpg", 0, make=None, model=None)) is None


def test_files_without_camera_follow_their_folder():
    items = [
        (Path("/a/1.jpg"), _meta("/a/1.jpg", 0, serial="A")),
        (Path("/a/2.jpg"), _meta("/a/2.jpg", 1, serial="A")),
        (Path("/a/clip.mp4"), None),
        (Path("/b/clip.mp4"), None),
    ]
    keys = camera_keys(items)
    assert keys[Path("/a/clip.mp4")] == "Canon EOS R5 #A"
    assert keys[Path("/b/clip.mp4")] is None


def test_shift_photos_moves_one_body_and_resorts():
    a1, a2 = _meta("/a/1.jpg", 0, serial="A"), _meta("/a/2.jpg", 10, serial="A")
    b1 = _meta("/b/1.jpg", 95, serial="B")  # B 시계가 90초 빠름: 실제로는 a1과 a2 사이

    shifted = shift_photos([a1, a2, b1], {"Canon EOS R5 #B": -90})

    assert [p.path.name for p in shifted] == ["1.jpg", "1.jpg", "2.jpg"]
    assert shifted[1].path == Path("/b/1.jpg")
    assert shifted[1].datetime_original == T0 + datetime.timedelta(seconds=5)
    assert b1.datetime_original == T0 + datetime.timedelta(seconds=95)  # 원본 그대로
    assert shift_photos([a1, b1], {}) == [a1, b1]


def test_offset_from_pair_matches_other_body_to_reference():
    reference = _meta("/a/x.jpg", 100, serial="A")
    other = _meta("/b/y.jpg", 183, serial="B")

    key, seconds = offset_from_pair(reference, other, {})
    assert (key, seconds) == ("Canon EOS R5 #B", -83.0)

    # 기준 바디에 이미 시차가 있으면 그 보정된 시각에 맞춘다
    assert offset_from_pair(reference, other, {"Canon EOS R5 #A": 10})[1] == -73.0


def test_offset_from_pair_rejects_same_body_or_unknown_camera():
    with pytest.raises(ValueError):
        offset_from_pair(_meta("a", 0, serial="A"), _meta("b", 5, serial="A"), {})
    with pytest.raises(ValueError):
        offset_from_pair(_meta("a", 0, make=None, model=None), _meta("b", 5, serial="B"), {})


def test_summarize_counts_bodies_and_unknown():
    photos = [_meta("/a/1.jpg", 0, serial="A"), _meta("/a/2.jpg", 9, serial="A"),
              _meta("/b/1.jpg", 4, serial="B"), _meta("/c/x.png", 2, make=None, model=None)]

    cameras, unknown = summarize(photos)

    assert [(c.key, c.count) for c in cameras] == [("Canon EOS R5 #A", 2), ("Canon EOS R5 #B", 1)]
    assert cameras[0].last - cameras[0].first == datetime.timedelta(seconds=9)
    assert unknown == 1


@pytest.mark.parametrize("seconds, text", [(0, "0:00"), (83, "+1:23"), (-5, "-0:05"), (7384, "+2:03:04")])
def test_format_and_parse_offset_round_trip(seconds, text):
    assert format_offset(seconds) == text
    assert parse_offset(text) == seconds


def test_parse_offset_rejects_garbage():
    for bad in ("1:2x", "+1:2:3:4", "25:00:00"):
        with pytest.raises(ValueError):
            parse_offset(bad)
