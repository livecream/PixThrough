import json

import pytest
from PIL import Image

import score_store
import scoring
from scanner import PhotoMetadata
from scoring import PhotoScore, score_photos


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    """실제 앱 데이터 폴더 대신 임시 폴더를 쓰고, 탭들이 공유하는 메모리 캐시도 비운다."""
    monkeypatch.setattr(score_store, "cache_dir", lambda: tmp_path / "cache")
    monkeypatch.setattr(score_store, "_score_cache", None)
    monkeypatch.setattr(score_store, "_hash_cache", None)


def _reload():
    """앱을 껐다 켠 것처럼 메모리 캐시를 버리고 디스크에서 다시 읽게 한다."""
    score_store._score_cache = None
    score_store._hash_cache = None


def _score(path, error=None):
    return PhotoScore(path=path, sharpness=1.5, exposure=0.9, face=None, total=1.5, error=error,
                      sharpness_global=2.0, sharpness_region="tiles")


def test_scores_and_hashes_survive_restart(tmp_path):
    photo = tmp_path / "a.jpg"
    score_store.score_cache()[photo] = (123.5, 42, _score(photo))
    score_store.hash_cache()[photo] = (123.5, 42, 0xABC)
    score_store.save()
    _reload()

    assert score_store.score_cache()[photo] == (123.5, 42, _score(photo))
    assert score_store.hash_cache()[photo] == (123.5, 42, 0xABC)


def test_failed_scores_and_missing_hashes_are_not_saved(tmp_path):
    broken = tmp_path / "broken.jpg"
    score_store.score_cache()[broken] = (1.0, 1, _score(broken, error="스코어링 실패"))
    score_store.hash_cache()[broken] = (1.0, 1, None)
    score_store.save()
    _reload()

    assert score_store.score_cache() == {}
    assert score_store.hash_cache() == {}


def test_saved_scores_are_ignored_after_scoring_version_changes(tmp_path, monkeypatch):
    photo = tmp_path / "a.jpg"
    score_store.score_cache()[photo] = (1.0, 1, _score(photo))
    score_store.save()
    _reload()
    monkeypatch.setattr(score_store, "SCORING_VERSION", score_store.SCORING_VERSION + 1)

    assert score_store.score_cache() == {}


def test_corrupt_store_file_is_treated_as_empty(tmp_path):
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "scores.json").write_text("{not json", encoding="utf-8")

    assert score_store.score_cache() == {}


def test_save_keeps_only_the_newest_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(score_store, "MAX_ENTRIES", 2)
    for name in "abc":
        score_store.hash_cache()[tmp_path / name] = (1.0, 1, 1)
    score_store.save()

    saved = json.loads((tmp_path / "cache" / "hashes.json").read_text(encoding="utf-8"))
    assert sorted(saved["entries"]) == [str(tmp_path / "b"), str(tmp_path / "c")]


def test_clear_empties_shared_caches_and_deletes_files(tmp_path):
    photo = tmp_path / "a.jpg"
    shared = score_store.score_cache()
    shared[photo] = (1.0, 1, _score(photo))
    score_store.save()

    score_store.clear()

    assert shared == {}  # 이미 이 딕셔너리를 들고 있는 탭도 함께 비워진다
    assert not (tmp_path / "cache" / "scores.json").exists()


def test_photos_scored_before_restart_are_not_scored_again(tmp_path, monkeypatch):
    photo = tmp_path / "a.png"
    Image.new("RGB", (32, 32), (128, 128, 128)).save(photo)
    meta = PhotoMetadata(path=photo, datetime_original=None, exif_source="exif")
    monkeypatch.setattr(scoring, "_detect_faces", lambda rgb: [])
    first = score_photos([meta], cache=score_store.score_cache())
    score_store.save()
    _reload()

    monkeypatch.setattr(scoring, "score_photo", lambda m: pytest.fail("다시 채점함"))
    second = score_photos([meta], cache=score_store.score_cache())

    assert second == first
