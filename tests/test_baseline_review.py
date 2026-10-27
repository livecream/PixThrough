import json
import sys

from PIL import Image

from baseline_review import diff_selection, format_diff, write_review_html
from report import ReportRow


def _row(path, scene, selected, sharpness=1.0):
    return ReportRow(path=path, scene_id=scene, sharpness=sharpness, exposure=0.9, face=None,
                     total=sharpness, selected=selected, reason="선택됨" if selected else "제외")


def test_diff_selection_lists_each_kind_of_change():
    old = {"a.jpg": True, "b.jpg": False, "gone.jpg": False}
    new = {"a.jpg": False, "b.jpg": True, "c.jpg": False}

    diff = diff_selection(old, new)

    assert (diff.added, diff.removed, diff.new_files, diff.missing_files) == (
        ["b.jpg"], ["a.jpg"], ["c.jpg"], ["gone.jpg"]
    )
    assert "새로 뽑힘 1장: b.jpg" in format_diff(diff)
    assert format_diff(diff_selection(new, new)) == "기준선과 같습니다."


def test_review_page_shows_only_changed_scenes(tmp_path):
    for name in ("a", "b", "c", "d"):
        Image.new("RGB", (40, 30), (100, 150, 200)).save(tmp_path / f"{name}.jpg")
    rows = [
        _row(tmp_path / "a.jpg", 1, False, 1.0), _row(tmp_path / "b.jpg", 1, True, 2.0),
        _row(tmp_path / "c.jpg", 2, True), _row(tmp_path / "d.jpg", 2, False, 0.5),
    ]
    old = {"a.jpg": True, "b.jpg": False, "c.jpg": True, "d.jpg": False}
    out = tmp_path / "review.html"

    scenes = write_review_html(rows, old, tmp_path, out)

    text = out.read_text(encoding="utf-8")
    assert scenes == 1
    assert "예전: a.jpg → 지금: b.jpg" in text
    assert "c.jpg" not in text  # 바뀌지 않은 장면은 빼고
    assert text.count("data:image/jpeg;base64,") == 2
    assert 'class="card now"' in text and 'class="card before"' in text


def test_review_page_without_baseline_shows_everything(tmp_path):
    Image.new("RGB", (40, 30)).save(tmp_path / "a.jpg")
    out = tmp_path / "review.html"

    assert write_review_html([_row(tmp_path / "a.jpg", 1, True)], None, tmp_path, out) == 1


def test_update_script_check_mode_writes_review_but_not_baseline(tmp_path, monkeypatch, capsys):
    import update_regression_baseline as script

    photos = tmp_path / "test_photos"
    photos.mkdir()
    Image.new("RGB", (64, 48), (120, 120, 120)).save(photos / "a.jpg")
    baseline = photos / "baseline.json"
    baseline.write_text(json.dumps({"a.jpg": False}), encoding="utf-8")
    monkeypatch.setattr(script, "TEST_PHOTOS_DIR", photos)
    monkeypatch.setattr(script, "BASELINE_PATH", baseline)
    monkeypatch.setattr(script, "REVIEW_PATH", photos / "baseline_review.html")
    monkeypatch.setattr(sys, "argv", ["update_regression_baseline.py", "--check"])

    script.main()

    out = capsys.readouterr().out
    assert "새로 뽑힘 1장: a.jpg" in out
    assert (photos / "baseline_review.html").exists()
    assert json.loads(baseline.read_text(encoding="utf-8")) == {"a.jpg": False}

    monkeypatch.setattr(sys, "argv", ["update_regression_baseline.py", "--yes"])
    script.main()
    assert json.loads(baseline.read_text(encoding="utf-8")) == {"a.jpg": True}
