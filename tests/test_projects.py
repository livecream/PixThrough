import datetime
from pathlib import Path

import projects
from history import Entry

NOW = datetime.datetime(2026, 11, 5, 12, 0)


def _entry(kind, folder, source=None, minutes=0):
    return Entry(NOW - datetime.timedelta(minutes=minutes), kind, f"{kind} 요약", folder, None, source)


def test_create_touch_rename_remove(tmp_path):
    store = tmp_path / "projects.json"
    shoot = tmp_path / "shoot"
    shoot.mkdir()
    a = projects.create("본식", shoot, store)
    b = projects.create("스냅", tmp_path, store)
    assert {p.name for p in projects.load(store)} == {"스냅", "본식"}
    again = projects.create("김OO 본식", shoot, store)  # 같은 폴더는 같은 촬영 건
    assert again.id == a.id and len(projects.load(store)) == 2
    projects.rename(b.id, "야외 스냅", store)
    assert {p.name for p in projects.load(store)} == {"김OO 본식", "야외 스냅"}
    projects.remove(a.id, store)
    assert [p.name for p in projects.load(store)] == ["야외 스냅"]
    assert shoot.exists()  # 폴더는 그대로


def test_progress_from_history_and_next_page(tmp_path):
    shoot = tmp_path / "shoot"
    (shoot / "2026-11-03").mkdir(parents=True)
    project = projects.Project("x", "본식", shoot, NOW, NOW)
    entries = [
        _entry("베스트컷 복사", tmp_path / "보정 진행 예정", source=shoot / "2026-11-03", minutes=1),  # 결과는 밖, 원본은 안
        _entry("시간순 정렬", shoot / "2026-11-03", minutes=5),
        _entry("카드 가져오기", shoot, minutes=10),
        _entry("사진 축소", tmp_path / "other"),  # 다른 촬영
    ]
    states = projects.progress(project, entries)
    assert [s.name for s in states if s.done_at] == ["가져오기", "시간순 정렬", "베스트컷"]
    assert projects.next_page(states) == "proof"
    assert projects.next_page(projects.progress(project, [])) == "rename"


def test_photo_folders(tmp_path):
    shoot = tmp_path / "shoot"
    (shoot / "2026-11-03").mkdir(parents=True)
    (shoot / "2026-11-03" / "a.jpg").write_bytes(b"x")
    (shoot / "2026-11-04").mkdir()
    (shoot / "2026-11-04" / "b.CR3").write_bytes(b"x")
    (shoot / "notes").mkdir()
    assert projects.photo_folders(shoot) == [shoot / "2026-11-03", shoot / "2026-11-04"]
    (shoot / "c.jpg").write_bytes(b"x")
    assert projects.photo_folders(shoot)[0] == shoot
    assert projects.photo_folders(tmp_path / "empty") == [tmp_path / "empty"]
