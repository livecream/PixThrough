import numpy as np
from PIL import Image

from image_hash import hash_distance, hashes_for, perceptual_hash


def _texture(seed, size=(256, 256)):
    rng = np.random.default_rng(seed)
    # 큰 덩어리 위주의 무늬(실사진처럼 저주파가 강함) — 32×32로 줄여도 형태가 남는다
    coarse = rng.integers(0, 256, size=(8, 8)).astype("uint8")
    return Image.fromarray(coarse).resize(size, Image.Resampling.BICUBIC).convert("RGB")


def test_hash_distance_counts_different_bits():
    assert hash_distance(0b1010, 0b1010) == 0
    assert hash_distance(0b1010, 0b0101) == 4


def test_same_photo_slightly_changed_stays_close(tmp_path):
    base = _texture(0)
    base.save(tmp_path / "a.jpg")
    brighter = Image.eval(base, lambda v: min(255, int(v * 1.15)))
    brighter.save(tmp_path / "b.jpg")

    distance = hash_distance(perceptual_hash(tmp_path / "a.jpg"), perceptual_hash(tmp_path / "b.jpg"))

    assert distance <= 6


def test_different_photos_are_far_apart(tmp_path):
    _texture(0).save(tmp_path / "a.jpg")
    _texture(1).save(tmp_path / "b.jpg")

    distance = hash_distance(perceptual_hash(tmp_path / "a.jpg"), perceptual_hash(tmp_path / "b.jpg"))

    assert distance >= 20


def test_perceptual_hash_is_none_for_unreadable_file(tmp_path):
    path = tmp_path / "broken.jpg"
    path.write_bytes(b"not an image")

    assert perceptual_hash(path) is None


def test_hashes_for_reuses_cache_for_unchanged_files(tmp_path, monkeypatch):
    path = tmp_path / "a.jpg"
    _texture(0).save(path)
    cache = {}
    first = hashes_for([path], cache=cache)

    import image_hash

    monkeypatch.setattr(image_hash, "perceptual_hash", lambda p: (_ for _ in ()).throw(AssertionError("다시 계산함")))
    second = hashes_for([path], cache=cache)

    assert first == second


def test_hashes_for_reports_missing_file_as_none(tmp_path):
    assert hashes_for([tmp_path / "gone.jpg"]) == {tmp_path / "gone.jpg": None}
