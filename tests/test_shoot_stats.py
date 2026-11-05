import datetime
from pathlib import Path

from scanner import PhotoMetadata
from shoot_stats import as_text, compute

T0 = datetime.datetime(2026, 11, 3, 10, 0, 0)


def _meta(name, minutes, model="EOS R5", iso="400", shutter="1/250", aperture="28/10", lens="RF24-70mm", focal=50.0):
    return PhotoMetadata(Path(name), T0 + datetime.timedelta(minutes=minutes), "exif", camera_model=model,
                         iso=iso, shutter_speed=shutter, aperture=aperture, lens=lens, focal_length=focal)


def test_compute_sections_and_badcut_ratio():
    photos = [
        _meta("a.jpg", 0), _meta("b.jpg", 10, shutter="1/30", iso="3200"), _meta("c.jpg", 70, model="EOS R6", lens=None),
        PhotoMetadata(Path("d.jpg"), T0, "mtime", error="이미지를 열 수 없음"),
    ]
    stats = compute(photos, flagged={Path("b.jpg")}, kept={Path("a.jpg"), Path("c.jpg")})
    assert stats.total == 3
    sections = {s.title: s for s in stats.sections}
    assert [(b.label, b.count) for b in sections["바디"].buckets] == [("EOS R5", 2), ("EOS R6", 1)]
    assert sections["바디"].buckets[0].extra == "남김 1 · C컷 1 (50%)"
    assert [(b.label, b.count) for b in sections["시간대"].buckets] == [("10시", 2), ("11시", 1)]
    assert [(b.label, b.count) for b in sections["셔터속도"].buckets] == [("1/250", 2), ("1/30", 1)]
    assert [(b.label, b.count) for b in sections["ISO"].buckets] == [("400", 2), ("3200", 1)]
    assert [(b.label, b.count) for b in sections["조리개"].buckets] == [("f/2.8", 3)]
    assert sections["렌즈"].note == "렌즈 정보 없음 1장"
    text = as_text(stats)
    assert "[바디]" in text and "EOS R5: 2장 (66%)" in text


def test_compute_without_badcut_has_note():
    stats = compute([_meta("a.jpg", 0, iso="[100, 0]", shutter=None, aperture=None, focal=None)])
    sections = {s.title: s for s in stats.sections}
    assert "C컷 기준" in sections["바디"].note
    assert [(b.label, b.count) for b in sections["ISO"].buckets] == [("100 이하", 1)]
    assert "셔터속도" not in sections and "초점거리" not in sections
