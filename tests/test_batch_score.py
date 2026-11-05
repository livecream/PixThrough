import pytest
from PIL import Image

import batch_score
import score_store


@pytest.fixture
def store(tmp_path, monkeypatch):
    cache = tmp_path / "appdata" / "cache"
    monkeypatch.setattr(score_store, "cache_dir", lambda: cache)
    monkeypatch.setattr(score_store, "_score_cache", None)
    monkeypatch.setattr(score_store, "_hash_cache", None)
    return cache


def test_prescore_fills_score_store_per_folder(tmp_path, store):
    a, b = tmp_path / "a", tmp_path / "b" / "2026-11-03"
    for folder, n in ((a, 2), (b, 3)):
        folder.mkdir(parents=True)
        for i in range(n):
            Image.new("RGB", (64, 48), (i * 40, 80, 120)).save(folder / f"IMG_{i}.jpg")
    (a / "broken.jpg").write_bytes(b"x")
    calls = []
    results = batch_score.prescore([a, tmp_path / "b", tmp_path / "missing"],
                                   on_progress=lambda *args: calls.append(args[:3]))
    assert [(r.photos, r.failed) for r in results[:2]] == [(3, 1), (3, 0)]
    assert results[2].photos == 0
    assert (1, 3, batch_score.STAGE_SCORE) in calls and (2, 3, batch_score.STAGE_SCORE) in calls
    # 저장소에 남아 다음에 다시 계산하지 않는다
    score_store._score_cache = None
    cached = score_store.score_cache()
    assert {p.name for p in cached} >= {"IMG_0.jpg", "IMG_1.jpg", "IMG_2.jpg"}
