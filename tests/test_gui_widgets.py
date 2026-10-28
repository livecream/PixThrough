from gui_widgets import format_remaining


def test_format_remaining_rounds_to_friendly_units():
    assert format_remaining(2) == "약 5초"
    assert format_remaining(23) == "약 25초"
    assert format_remaining(59) == "약 60초"
    assert format_remaining(61) == "약 1분"
    assert format_remaining(150) == "약 2분"  # 2.5분 → 짝수 반올림
    assert format_remaining(3600) == "약 1시간"
    assert format_remaining(3900) == "약 1시간 5분"
