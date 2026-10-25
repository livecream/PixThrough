import csv
import datetime
import json

from PIL import Image

from pathlib import Path

from report import (
    MANUAL_ADDED,
    MANUAL_REMOVED,
    ReportRow,
    apply_manual_choices,
    build_report,
    write_report,
)
from scanner import PhotoMetadata


def _make_flat(path, size=32, color=128):
    Image.new("RGB", (size, size), (color, color, color)).save(path, "png")
    return path


def _meta(path, seconds_offset=0):
    base = datetime.datetime(2026, 6, 1, 14, 0, 0)
    return PhotoMetadata(
        path=path,
        datetime_original=base + datetime.timedelta(seconds=seconds_offset),
        exif_source="exif",
    )


def _two_photo_scene(tmp_path):
    p1 = _make_flat(tmp_path / "a.png")
    p2 = _make_flat(tmp_path / "b.png")
    return [[_meta(p1, 0), _meta(p2, 1)]]


def test_build_report_marks_exactly_one_selected_per_scene(tmp_path):
    groups = _two_photo_scene(tmp_path)

    rows = build_report(groups)

    assert len(rows) == 2
    assert sum(1 for r in rows if r.selected) == 1


def test_build_report_selected_row_has_selection_reason(tmp_path):
    groups = _two_photo_scene(tmp_path)

    rows = build_report(groups)
    selected = next(r for r in rows if r.selected)

    assert "선택" in selected.reason


def test_build_report_excluded_row_has_exclusion_reason(tmp_path):
    groups = _two_photo_scene(tmp_path)

    rows = build_report(groups)
    excluded = next(r for r in rows if not r.selected)

    assert "제외" in excluded.reason


def test_build_report_assigns_scene_id_per_group(tmp_path):
    p1 = _make_flat(tmp_path / "scene1.png")
    p2 = _make_flat(tmp_path / "scene2.png")
    groups = [[_meta(p1, 0)], [_meta(p2, 100)]]

    rows = build_report(groups)

    assert [r.scene_id for r in rows] == [1, 2]


def test_write_report_csv_contains_expected_columns(tmp_path):
    groups = _two_photo_scene(tmp_path)
    rows = build_report(groups)
    out = tmp_path / "report.csv"

    write_report(rows, out)

    with open(out, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        records = list(reader)

    assert len(records) == 2
    assert set(records[0].keys()) >= {
        "path", "scene_id", "sharpness", "exposure", "face", "total", "selected", "reason",
    }


def test_write_report_json_round_trips(tmp_path):
    groups = _two_photo_scene(tmp_path)
    rows = build_report(groups)
    out = tmp_path / "report.json"

    write_report(rows, out)

    with open(out, encoding="utf-8") as f:
        records = json.load(f)

    assert len(records) == 2
    assert records[0]["scene_id"] == 1


def test_write_report_rejects_unsupported_extension(tmp_path):
    groups = _two_photo_scene(tmp_path)
    rows = build_report(groups)

    try:
        write_report(rows, tmp_path / "report.txt")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_build_report_reports_progress_across_all_scenes(tmp_path):
    p1 = _make_flat(tmp_path / "scene1.png")
    p2 = _make_flat(tmp_path / "scene2a.png")
    p3 = _make_flat(tmp_path / "scene2b.png")
    groups = [[_meta(p1, 0)], [_meta(p2, 100), _meta(p3, 101)]]
    calls = []

    build_report(groups, on_progress=lambda done, total: calls.append((done, total)))

    assert calls == [(1, 3), (2, 3), (3, 3)]


def _report_row(name, selected, reason="선택됨: 장면 내 최고 종합점수"):
    return ReportRow(
        path=Path(name), scene_id=1, sharpness=1.0, exposure=1.0, face=None,
        total=1.0, selected=selected, reason=reason if selected else "제외: 종합 점수가 더 낮음",
    )


def test_apply_manual_choices_adds_extra_cut_and_removes_auto_cut():
    rows = [_report_row("a.jpg", True), _report_row("b.jpg", False), _report_row("c.jpg", False)]

    result = apply_manual_choices(rows, {Path("a.jpg"): False, Path("b.jpg"): True})

    assert [r.selected for r in result] == [False, True, False]
    assert result[0].manual_choice == MANUAL_REMOVED
    assert result[0].reason.startswith("제외: 직접")
    assert result[1].manual_choice == MANUAL_ADDED
    assert result[1].reason.startswith("선택됨: 직접 추가")
    assert result[2] == rows[2]


def test_apply_manual_choices_ignores_choices_matching_auto_result():
    """장면 간격을 바꿔 직접 추가한 컷이 자동으로도 뽑히면, 그냥 자동 선택으로 남는다."""
    rows = [_report_row("a.jpg", True), _report_row("b.jpg", False)]

    result = apply_manual_choices(rows, {Path("a.jpg"): True, Path("b.jpg"): False})

    assert result == rows


def test_apply_manual_choices_ignores_paths_not_in_rows():
    rows = [_report_row("a.jpg", True)]

    assert apply_manual_choices(rows, {Path("gone.jpg"): True}) == rows


def test_write_report_includes_manual_choice_column(tmp_path):
    rows = apply_manual_choices([_report_row("a.jpg", False)], {Path("a.jpg"): True})
    out = tmp_path / "report.csv"

    write_report(rows, out)

    with open(out, encoding="utf-8-sig") as f:
        record = next(csv.DictReader(f))
    assert record["manual_choice"] == MANUAL_ADDED


def test_build_report_keeps_proportional_cuts_with_reasons(tmp_path):
    paths = [_make_flat(tmp_path / f"{n}.png", color=100 + i) for i, n in enumerate("abcdef")]
    groups = [[_meta(p, i) for i, p in enumerate(paths)]]

    rows = build_report(groups, keep_percent=20)  # 6장 × 20% = 1.2 → 올림 2장

    selected = [r for r in rows if r.selected]
    assert len(selected) == 2
    reasons = sorted(r.reason for r in selected)
    assert reasons[0].startswith("선택됨: 장면 내 2위 (장면 6장의 20% 올림 → 2장 남김)")
    assert reasons[1] == "선택됨: 장면 내 최고 종합점수"


def test_build_report_default_keeps_one_per_scene(tmp_path):
    rows = build_report(_two_photo_scene(tmp_path))

    assert sum(r.selected for r in rows) == 1


def test_build_report_selects_nothing_in_scene_of_broken_photos(tmp_path):
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"not an image")

    rows = build_report([[_meta(broken, 0)]])

    assert rows[0].selected is False
    assert rows[0].reason.startswith("제외: 점수 계산 실패")


def test_write_apply_report_saves_every_row_with_reasons(tmp_path):
    from report import write_apply_report

    rows = build_report(_two_photo_scene(tmp_path))
    out = tmp_path / "out"

    path = write_apply_report(rows, out, "bestcut")

    assert path.parent == out and path.name.startswith("bestcut_report_") and path.suffix == ".csv"
    with open(path, encoding="utf-8-sig") as f:
        records = list(csv.DictReader(f))
    assert len(records) == 2  # 선택된 사진만이 아니라 제외된 사진도
    assert {r["reason"].split(":")[0] for r in records} == {"선택됨", "제외"}


def test_csv_report_starts_with_bom_so_excel_reads_korean(tmp_path):
    rows = build_report(_two_photo_scene(tmp_path))
    out = tmp_path / "report.csv"

    write_report(rows, out)

    raw = out.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert "선택됨" in raw.decode("utf-8-sig")
