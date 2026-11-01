import json

import pytest

import history
from file_ops import apply_selection, undo_moves
from report import ReportRow


@pytest.fixture
def history_file(tmp_path):
    path = history.enable(tmp_path / "data" / "history.jsonl")
    yield path
    history.disable()


def _row(path):
    return ReportRow(path=path, scene_id=1, sharpness=1, exposure=1, face=None, total=1, selected=True, reason="x")


def test_nothing_is_written_until_enabled(tmp_path):
    history.disable()
    history.record("복사", "1장")
    assert history.load() == []
    assert not any(tmp_path.iterdir())


def test_file_operations_are_recorded_newest_first(tmp_path, history_file):
    src = tmp_path / "shoot"
    src.mkdir()
    (src / "a.jpg").write_bytes(b"a")
    out = tmp_path / "보정"
    results = apply_selection([_row(src / "a.jpg")], out, move=True, purpose="C컷 이동")
    undo_moves(results, out)

    entries = history.load()
    assert [e.kind for e in entries] == ["이동 되돌리기", "C컷 이동"]
    moved = entries[1]
    assert moved.summary == "1장 이동 → 보정" and moved.folder == out
    assert moved.log_file.name.startswith("apply_log_") and moved.log_file.exists()
    assert entries[0].folder == src


def test_broken_lines_are_skipped_and_file_is_trimmed(history_file, monkeypatch):
    history.record("복사", "첫 줄")
    with open(history_file, "a", encoding="utf-8") as f:
        f.write("{깨진 줄\n")
    history.record("이동", "둘째 줄", log_file=history_file)
    assert [e.summary for e in history.load()] == ["둘째 줄", "첫 줄"]

    monkeypatch.setattr(history, "_TRIM_BYTES", 300)
    monkeypatch.setattr(history, "MAX_ENTRIES", 3)
    for i in range(10):
        history.record("복사", f"{i}번째")
    lines = history_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 4 and json.loads(lines[-1])["summary"] == "9번째"
