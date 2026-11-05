import json

import pytest
from PySide6.QtCore import QSettings

import choice_store
import score_store
import settings_transfer


@pytest.fixture
def store(tmp_path, monkeypatch):
    cache = tmp_path / "appdata" / "cache"
    monkeypatch.setattr(score_store, "cache_dir", lambda: cache)
    choice_store.clear_memory()
    yield cache
    choice_store.clear_memory()


def _settings(path):
    return QSettings(str(path), QSettings.Format.IniFormat)


def test_export_then_import_on_fresh_settings_restores_values_and_records(tmp_path, store):
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"x")
    other = tmp_path / "b.jpg"
    other.write_bytes(b"y")
    choice_store.choices.sync([photo], {photo: True})
    choice_store.scene_edits.sync([other], {other: "split"})
    choice_store.client_picks.sync([photo], {photo: "이 컷 좋아요"})
    old = _settings(tmp_path / "old.ini")
    old.setValue("bestcut/scene_gap_seconds", 4.5)
    old.setValue("selection/input_dirs", [str(tmp_path)])
    old.setValue("window/geometry", b"\x01\x02")
    import projects
    projects.create("본식", tmp_path)
    exported = tmp_path / "export" / "pixthrough-settings.json"
    summary = settings_transfer.export_to(old, exported)
    data = json.loads(exported.read_text(encoding="utf-8"))
    assert "window/geometry" not in data["settings"]
    assert summary.records == {"choices": 1, "scene_edits": 1, "client_picks": 1, "ratings": 0, "projects": 1}
    assert set(data["records"]) == {"choices", "scene_edits", "client_picks", "ratings"}  # 점수 저장소는 넣지 않음

    # 다른 컴퓨터(빈 설정·빈 기록)에 가져오기
    for name in ("choices.json", "scene_edits.json", "client_picks.json"):
        (store / name).unlink()
    (store.parent / "projects.json").unlink()
    choice_store.clear_memory()
    new = _settings(tmp_path / "new.ini")
    new.setValue("bestcut/view", "table")  # 파일에 없는 설정은 그대로
    imported, backup = settings_transfer.import_from(new, exported)
    assert float(new.value("bestcut/scene_gap_seconds")) == 4.5
    assert new.value("bestcut/view") == "table"
    assert imported.records == {"choices": 1, "scene_edits": 1, "client_picks": 1, "ratings": 0, "projects": 1}
    assert [p.name for p in projects.load()] == ["본식"]
    assert choice_store.choices.restore([photo]) == {photo: True}
    assert choice_store.scene_edits.restore([other]) == {other: "split"}
    assert choice_store.client_picks.restore([photo]) == {photo: "이 컷 좋아요"}
    # 가져오기 전 상태 백업
    assert json.loads(backup.read_text(encoding="utf-8"))["settings"]["bestcut/view"] == "table"


def test_import_skips_malformed_records(tmp_path, store):
    assert choice_store.choices.import_entries({"/x.jpg": [1.0, 2, "not bool"], "/y.jpg": [1.0, 2, True], 3: []}) == 1


def test_read_file_rejects_other_files(tmp_path):
    bad = tmp_path / "x.json"
    bad.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="설정 파일이 아닙니다"):
        settings_transfer.read_file(bad)
    bad.write_text("not json", encoding="utf-8")
    with pytest.raises(ValueError):
        settings_transfer.read_file(bad)
    bad.write_text(json.dumps({"app": "PixThrough", "format": 99}), encoding="utf-8")
    with pytest.raises(ValueError, match="업데이트"):
        settings_transfer.read_file(bad)
