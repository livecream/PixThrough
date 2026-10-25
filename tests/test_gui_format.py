import datetime
from pathlib import Path

from file_ops import FileOpResult
from gui_format import (
    bestcut_grouping_help,
    bestcut_scoring_help,
    format_apply_summary,
    format_badcut_header,
    format_badcut_preview,
    format_duplicate_report,
    format_folder_summary,
    format_manual_choice_summary,
    format_preview_header,
    format_preview_summary,
    format_rename_apply_summary,
    format_rename_preview,
    format_threshold_reference,
)
from organize import DuplicateGroup, FolderSummary
from rename_by_time import RenamePlanRow, RenameResult
from report import ReportRow


def _row(path, scene_id, selected, reason):
    return ReportRow(
        path=Path(path),
        scene_id=scene_id,
        sharpness=1.0,
        exposure=1.0,
        face=None,
        total=1.0,
        selected=selected,
        reason=reason,
    )


def test_format_preview_summary_counts_scenes_and_selections():
    rows = [
        _row("a.jpg", 1, True, "선택됨: 장면 내 최고 종합점수"),
        _row("b.jpg", 1, False, "제외: 종합 점수가 더 낮음"),
        _row("c.jpg", 2, True, "선택됨: 장면 내 최고 종합점수"),
    ]

    summary = format_preview_summary(rows)

    assert "2개 장면" in summary
    assert "3장 중 2장 선택" in summary


def test_format_preview_summary_lists_selected_file_per_scene():
    rows = [
        _row("a.jpg", 1, True, "선택됨: 장면 내 최고 종합점수"),
        _row("b.jpg", 1, False, "제외: 종합 점수가 더 낮음"),
    ]

    summary = format_preview_summary(rows)

    assert "[장면 1] 선택: a.jpg" in summary
    assert "제외: b.jpg" in summary


def test_format_preview_summary_handles_empty_rows():
    assert "0개 장면" in format_preview_summary([])


def test_format_apply_summary_reports_count_and_output_dir():
    results = [
        FileOpResult(
            source=Path("a.jpg"),
            destination=Path("/out/a.jpg"),
            raw_source=None,
            raw_destination=None,
            action="copy",
        )
    ]

    summary = format_apply_summary(results, Path("/out"))

    assert "1장" in summary
    # Windows에서는 Path("/out")이 "\\out"으로 출력되므로 OS 구분자에 맞춰 비교
    assert str(Path("/out")) in summary


def test_format_apply_summary_reports_move_label_for_move_action():
    results = [
        FileOpResult(
            source=Path("a.jpg"),
            destination=Path("/out/a.jpg"),
            raw_source=None,
            raw_destination=None,
            action="move",
        )
    ]

    summary = format_apply_summary(results, Path("/out"))

    assert "이동" in summary
    assert "복사" not in summary


def _rename_row(old_name, new_name, raw_old_name=None, raw_new_name=None):
    old_path = Path("/in") / old_name
    return RenamePlanRow(
        seq=1,
        old_path=old_path,
        new_path=Path("/in") / new_name,
        raw_old_path=Path("/in") / raw_old_name if raw_old_name else None,
        raw_new_path=Path("/in") / raw_new_name if raw_new_name else None,
        datetime_original=datetime.datetime(2026, 1, 1, 10, 0, 0),
    )


def test_format_rename_preview_lists_old_and_new_names():
    rows = [_rename_row("DSC_001.jpg", "00001.jpg")]

    summary = format_rename_preview(rows)

    assert "1개 파일 리네임 예정" in summary
    assert "DSC_001.jpg → 00001.jpg" in summary


def test_format_rename_preview_shows_raw_pairing():
    rows = [_rename_row("DSC_001.jpg", "00001.jpg", "DSC_001.cr3", "00001.cr3")]

    summary = format_rename_preview(rows)

    assert "DSC_001.cr3 → 00001.cr3" in summary


def test_format_rename_apply_summary_reports_count():
    results = [
        RenameResult(
            old_path=Path("/in/a.jpg"),
            new_path=Path("/in/00001.jpg"),
            raw_old_path=None,
            raw_new_path=None,
        )
    ]

    assert "1개 파일" in format_rename_apply_summary(results)


def test_format_badcut_preview_counts_and_lists_flagged_only():
    rows = [
        _row("a.jpg", 0, True, "C컷: 블러(선명도 낮음)"),
        _row("b.jpg", 0, False, "정상 컷(임계값 이상)"),
    ]

    summary = format_badcut_preview(rows)

    assert "2장 중 1장 C컷 판정" in summary
    assert "a.jpg" in summary
    assert "b.jpg" not in summary


def test_format_folder_summary_includes_counts_and_readable_size():
    summary = FolderSummary(
        total_files=10,
        total_size_bytes=5 * 1024 * 1024,
        photo_count=4,
        raw_count=3,
        video_count=2,
        other_count=1,
    )

    text = format_folder_summary(summary)

    assert "10" in text
    assert "5.0MB" in text
    assert "사진 4개" in text
    assert "RAW 3개" in text
    assert "영상 2개" in text
    assert "기타 1개" in text


def test_format_duplicate_report_lists_keep_and_candidates():
    groups = [
        DuplicateGroup(keep=Path("a.jpg"), duplicates=[Path("b.jpg"), Path("c.jpg")]),
    ]

    text = format_duplicate_report(groups)

    assert "중복 그룹 1개, 삭제 후보 2개" in text
    assert "보존: a.jpg" in text
    assert "삭제 후보: b.jpg" in text
    assert "삭제 후보: c.jpg" in text


def test_format_duplicate_report_empty_groups():
    text = format_duplicate_report([])

    assert "중복 그룹 0개, 삭제 후보 0개" in text


def test_format_preview_header_counts_scenes_and_selection():
    rows = [
        _row("a.jpg", 1, True, "선택됨"),
        _row("b.jpg", 1, False, "제외"),
        _row("c.jpg", 2, True, "선택됨"),
    ]

    header = format_preview_header(rows)

    assert "2개 장면" in header
    assert "3장 중 2장 선택" in header


def test_format_badcut_header_counts_and_no_scores_when_all_error():
    rows = [
        _row("a.jpg", 0, False, "판정 불가: 손상"),
    ]

    header = format_badcut_header(rows)

    assert "1장 중 0장 C컷 판정" in header


def test_format_threshold_reference_names_reference_photo():
    text = format_threshold_reference("IMG_0012.JPG", 152.34, 1)

    assert "IMG_0012.JPG" in text
    assert "152.3" in text


def test_format_threshold_reference_marks_manual_value():
    text = format_threshold_reference(None, 0.5, 2)

    assert "직접 입력" in text
    assert "0.50" in text


def test_bestcut_grouping_help_shows_gap_seconds():
    assert "5초 이내" in bestcut_grouping_help(5)


def test_bestcut_scoring_help_shows_weights_as_percent():
    text = bestcut_scoring_help({"sharpness": 0.5, "exposure": 0.3, "face": 0.2})

    assert "선명도 50%" in text
    assert "노출 30%" in text
    assert "눈뜸 20%" in text


def test_format_preview_header_mentions_scene_gap_when_given():
    rows = [_row("a.jpg", 1, True, "선택됨"), _row("b.jpg", 2, True, "선택됨")]

    assert format_preview_header(rows, 4.5) == "2개 장면(간격 4.5초), 2장 중 2장 선택"
    assert format_preview_header(rows) == "2개 장면, 2장 중 2장 선택"


def test_format_manual_choice_summary_counts_added_and_removed():
    from dataclasses import replace

    rows = [
        replace(_row("a.jpg", 1, True, "선택됨: 직접 추가"), manual_choice="added"),
        replace(_row("b.jpg", 1, False, "제외: 직접 뺌"), manual_choice="removed"),
        replace(_row("c.jpg", 2, True, "선택됨: 직접 추가"), manual_choice="added"),
        _row("d.jpg", 2, False, "제외: 종합 점수가 더 낮음"),
    ]

    assert format_manual_choice_summary(rows) == "직접 추가 2장 · 직접 뺌 1장"
    assert format_manual_choice_summary(rows[3:]) == ""


def test_format_preview_header_mentions_similarity_split_when_on():
    rows = [_row("a.jpg", 1, True, "선택됨")]

    assert format_preview_header(rows, 3, 20) == "1개 장면(간격 3초, 차이 20 넘으면 나눔), 1장 중 1장 선택"


def test_bestcut_grouping_help_describes_split_setting():
    from gui_format import bestcut_grouping_help

    assert "꺼져 있습니다" in bestcut_grouping_help(3)
    assert "<b>20</b>를 넘으면" in bestcut_grouping_help(3, 20)
