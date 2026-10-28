from gui_errors import explain, split_message


def test_explain_common_os_errors_in_korean():
    assert "디스크 공간" in explain("[Errno 28] No space left on device: '/Volumes/X/a.jpg'")
    assert "권한" in explain("[Errno 13] Permission denied: '/a'")
    assert "다른 앱" in explain("[WinError 32] The process cannot access the file because it is being used by another process")
    assert "읽기 전용" in explain("[Errno 30] Read-only file system: '/Volumes/SD'")
    assert "찾을 수 없습니다" in explain("[Errno 2] No such file or directory: 'x'")
    assert explain("출력 폴더가 입력 폴더와 같거나 그 안에 포함되어 있습니다") is None


def test_split_message_keeps_first_line_and_moves_long_list_to_details():
    head, hint, details = split_message(
        "2개 파일을 옮기지 못했습니다 (나머지 3개는 완료).\na.jpg: [Errno 28] No space left on device\nb.jpg: ..."
    )
    assert head == "2개 파일을 옮기지 못했습니다 (나머지 3개는 완료)."
    assert "디스크 공간" in hint
    assert "a.jpg" in details
    assert split_message("짧은 오류") == ("짧은 오류", None, None)
