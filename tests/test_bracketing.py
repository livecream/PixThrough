import datetime
from pathlib import Path

from bracketing import find_brackets, keep_brackets
from report import ReportRow
from scanner import PhotoMetadata

T0 = datetime.datetime(2026, 11, 3, 10, 0, 0)


def _meta(name, seconds, ev, model="R5"):
    return PhotoMetadata(path=Path(name), datetime_original=T0 + datetime.timedelta(seconds=seconds),
                         exif_source="exif", camera_model=model, exposure_bias=ev)


def test_finds_aeb_runs_and_ignores_normal_bursts():
    photos = [
        _meta("a1.jpg", 0, 0.0), _meta("a2.jpg", 0.3, -2.0), _meta("a3.jpg", 0.6, 2.0),  # AEB 3장
        _meta("b1.jpg", 10, 0.0), _meta("b2.jpg", 10.2, 0.0), _meta("b3.jpg", 10.4, 0.0),  # 같은 노출 연사
        _meta("c1.jpg", 20, -1.0), _meta("c2.jpg", 25, 0.0), _meta("c3.jpg", 30, 1.0),  # 손으로 바꿈(느림)
        _meta("d1.jpg", 40, 0.0), _meta("d2.jpg", 40.5, -1.0),  # 2장뿐
        _meta("e1.jpg", 50, None), _meta("e2.jpg", 50.2, None), _meta("e3.jpg", 50.4, None),  # 노출 보정 모름
        _meta("f1.jpg", 60, 0.0), _meta("f2.jpg", 60.3, -0.7), _meta("f3.jpg", 60.6, 0.7),
        _meta("f4.jpg", 60.9, 0.0),  # 다음 AEB의 시작(같은 값이 다시 나오면 새 묶음)
    ]
    brackets = find_brackets(photos)
    assert sorted(str(p) for p in brackets) == ["a1.jpg", "a2.jpg", "a3.jpg", "f1.jpg", "f2.jpg", "f3.jpg"]
    assert brackets[Path("a2.jpg")].describe() == "브라케팅 1 (2/3장, 0 · −2 · +2 EV)"
    assert brackets[Path("f3.jpg")].number == 2


def test_bodies_are_separate():
    photos = [_meta("x1.jpg", 0, 0.0, "R5"), _meta("y1.jpg", 0.2, -2.0, "R6"), _meta("x2.jpg", 0.4, 2.0, "R5")]
    assert find_brackets(photos) == {}


def test_keep_brackets_selects_whole_bracket_except_unscored():
    photos = [_meta("a1.jpg", 0, 0.0), _meta("a2.jpg", 0.3, -2.0), _meta("a3.jpg", 0.6, 2.0)]
    rows = [
        ReportRow(Path("a1.jpg"), 1, 1.0, 0.9, None, 0.9, True, "선택됨: 장면 내 1위"),
        ReportRow(Path("a2.jpg"), 1, 1.0, 0.3, None, 0.5, False, "제외: 장면 내 2위"),
        ReportRow(Path("a3.jpg"), 1, 0.0, 0.0, None, 0.0, False, "제외: 점수 계산 실패 (x)"),
        ReportRow(Path("z.jpg"), 2, 1.0, 0.9, None, 0.9, False, "제외: 장면 내 2위"),
    ]
    kept = keep_brackets(rows, find_brackets(photos))
    assert [r.selected for r in kept] == [True, True, False, False]
    assert kept[1].reason.startswith("선택됨: 브라케팅 묶음 통째로 남김")
    assert kept[0] is rows[0]
