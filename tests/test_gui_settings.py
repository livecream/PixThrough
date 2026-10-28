from PySide6.QtCore import QSettings

from gui_settings import (
    load_float,
    load_folder,
    load_folder_list,
    save_float,
    save_folder,
    save_folder_list,
)


def _settings(tmp_path) -> QSettings:
    return QSettings(str(tmp_path / "test_settings.ini"), QSettings.Format.IniFormat)


def test_save_and_load_folder_list_round_trip(tmp_path):
    settings = _settings(tmp_path)
    folder_a = tmp_path / "a"
    folder_b = tmp_path / "b"
    folder_a.mkdir()
    folder_b.mkdir()

    save_folder_list(settings, "test/dirs", [folder_a, folder_b])
    settings.sync()

    assert load_folder_list(settings, "test/dirs") == [folder_a, folder_b]


def test_save_and_load_single_item_folder_list_round_trip(tmp_path):
    # QSettings는 1개짜리 리스트를 문자열로 돌려주는 경우가 있어 별도로 검증한다.
    settings = _settings(tmp_path)
    folder = tmp_path / "a"
    folder.mkdir()

    save_folder_list(settings, "test/dirs", [folder])
    settings.sync()

    assert load_folder_list(settings, "test/dirs") == [folder]


def test_load_folder_list_treats_single_string_value_as_one_item_list(tmp_path):
    """Qt/PySide6는 백엔드에 따라 1개짜리 리스트를 저장했다가 읽으면 문자열
    하나로 돌려주기도 한다 — 이 플랫폼에 의존하지 않고 그 분기를 직접 검증한다."""
    folder = tmp_path / "a"
    folder.mkdir()

    class FakeSettings:
        def value(self, key, default):
            return str(folder)

    assert load_folder_list(FakeSettings(), "test/dirs") == [folder]


def test_load_folder_list_filters_out_missing_paths(tmp_path):
    settings = _settings(tmp_path)
    existing = tmp_path / "exists"
    existing.mkdir()
    missing = tmp_path / "missing"

    save_folder_list(settings, "test/dirs", [existing, missing])
    settings.sync()

    assert load_folder_list(settings, "test/dirs") == [existing]


def test_load_folder_list_returns_empty_when_key_unset(tmp_path):
    settings = _settings(tmp_path)

    assert load_folder_list(settings, "test/unset") == []


def test_save_and_load_folder_round_trip(tmp_path):
    settings = _settings(tmp_path)
    folder = tmp_path / "a"
    folder.mkdir()

    save_folder(settings, "test/dir", folder)
    settings.sync()

    assert load_folder(settings, "test/dir") == folder


def test_load_folder_returns_none_when_missing_on_disk(tmp_path):
    settings = _settings(tmp_path)
    missing = tmp_path / "missing"

    save_folder(settings, "test/dir", missing)
    settings.sync()

    assert load_folder(settings, "test/dir") is None


def test_load_folder_returns_none_when_unset(tmp_path):
    settings = _settings(tmp_path)

    assert load_folder(settings, "test/unset") is None


def test_save_and_load_float_round_trip_through_ini_file(tmp_path):
    save_float(_settings(tmp_path), "test/gap", 4.5)

    # 새로 연 QSettings(ini)는 값을 문자열로 돌려준다 — 숫자로 바꿔 읽어야 한다
    assert load_float(_settings(tmp_path), "test/gap", 3.0, 0.5, 60.0) == 4.5


def test_load_float_uses_default_when_unset(tmp_path):
    assert load_float(_settings(tmp_path), "test/gap", 3.0, 0.5, 60.0) == 3.0


def test_load_float_uses_default_for_broken_or_out_of_range_value(tmp_path):
    settings = _settings(tmp_path)

    settings.setValue("test/gap", "abc")
    assert load_float(settings, "test/gap", 3.0, 0.5, 60.0) == 3.0

    settings.setValue("test/gap", 999)
    assert load_float(settings, "test/gap", 3.0, 0.5, 60.0) == 3.0


def test_save_and_load_bool_round_trip_through_ini_file(tmp_path):
    from gui_settings import load_bool, save_bool

    save_bool(_settings(tmp_path), "test/flag", True)

    assert load_bool(_settings(tmp_path), "test/flag", False) is True
    assert load_bool(_settings(tmp_path), "test/unset", False) is False


def test_saving_folders_records_recent_folders_newest_first(tmp_path):
    from gui_settings import MAX_RECENT, add_recent, clear_recent, recent_folders, save_folder

    settings = _settings(tmp_path)
    folders = []
    for i in range(MAX_RECENT + 2):
        folder = tmp_path / f"f{i}"
        folder.mkdir()
        folders.append(folder)
    save_folder(settings, "a/out", folders[0])
    save_folder_list(settings, "a/in", [folders[1], folders[2]])
    assert recent_folders(settings)[:3] == [folders[1], folders[2], folders[0]]

    add_recent(settings, folders[0])  # 다시 쓰면 맨 앞으로(중복 없음)
    assert recent_folders(settings)[:3] == [folders[0], folders[1], folders[2]]

    for folder in folders:
        add_recent(settings, folder)
    assert len(recent_folders(settings)) == MAX_RECENT
    folders[-1].rmdir()  # 없어진 폴더는 빠짐
    assert folders[-1] not in recent_folders(settings)

    save_folder(settings, "a/out", None)  # 지운 설정은 최근 폴더에 안 남김
    clear_recent(settings)
    assert recent_folders(settings) == []
