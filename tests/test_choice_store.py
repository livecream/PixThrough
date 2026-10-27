import os

import pytest

import choice_store
import score_store


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(score_store, "cache_dir", lambda: tmp_path / "cache")
    choice_store.clear_memory()
    yield
    choice_store.clear_memory()


def _photo(tmp_path, name, data=b"x"):
    path = tmp_path / name
    path.write_bytes(data)
    return path


def test_choices_survive_restart(tmp_path):
    a, b, c = (_photo(tmp_path, n) for n in ("a.jpg", "b.jpg", "c.jpg"))
    choice_store.sync([a, b, c], {a: True, b: False})
    choice_store.clear_memory()

    assert choice_store.restore([a, b, c]) == {a: True, b: False}


def test_sync_drops_choices_that_went_back_to_auto(tmp_path):
    a, b = _photo(tmp_path, "a.jpg"), _photo(tmp_path, "b.jpg")
    choice_store.sync([a, b], {a: True, b: True})
    choice_store.sync([a, b], {b: True})
    choice_store.clear_memory()

    assert choice_store.restore([a, b]) == {b: True}


def test_sync_keeps_choices_of_photos_not_on_screen(tmp_path):
    a, b = _photo(tmp_path, "a.jpg"), _photo(tmp_path, "b.jpg")
    choice_store.sync([a], {a: True})
    choice_store.sync([b], {b: False})

    assert choice_store.restore([a, b]) == {a: True, b: False}


def test_changed_file_loses_its_choice(tmp_path):
    a = _photo(tmp_path, "a.jpg")
    choice_store.sync([a], {a: True})
    a.write_bytes(b"different size")
    os.utime(a, (1, 1))

    assert choice_store.restore([a]) == {}


def test_forget_removes_moved_photos(tmp_path):
    a = _photo(tmp_path, "a.jpg")
    choice_store.sync([a], {a: True})
    choice_store.forget([a])
    choice_store.clear_memory()

    assert choice_store.restore([a]) == {}


def test_broken_file_is_ignored(tmp_path):
    a = _photo(tmp_path, "a.jpg")
    path = tmp_path / "cache" / "choices.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")

    assert choice_store.restore([a]) == {}
    choice_store.sync([a], {a: True})
    assert choice_store.restore([a]) == {a: True}


def test_oldest_choices_are_dropped_past_the_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(choice_store, "MAX_ENTRIES", 2)
    a, b, c = (_photo(tmp_path, n) for n in ("a.jpg", "b.jpg", "c.jpg"))
    for photo in (a, b, c):
        choice_store.sync([photo], {photo: True})

    assert choice_store.restore([a, b, c]) == {b: True, c: True}
