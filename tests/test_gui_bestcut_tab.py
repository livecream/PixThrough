import os
from datetime import datetime, timedelta

import numpy as np
import pytest
from PIL import Image

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtCore = pytest.importorskip("PySide6.QtCore")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

import gui_bestcut_tab  # noqa: E402
from scanner import PhotoMetadata  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def tab(app, tmp_path, monkeypatch):
    ini = str(tmp_path / "settings.ini")
    monkeypatch.setattr(
        gui_bestcut_tab,
        "make_settings",
        lambda: QtCore.QSettings(ini, QtCore.QSettings.Format.IniFormat),
    )
    return gui_bestcut_tab.BestCutTab()


def _photos(tmp_path, offsets_seconds):
    start = datetime(2026, 10, 18, 12, 0, 0)
    photos = []
    for i, offset in enumerate(offsets_seconds):
        path = tmp_path / f"IMG_{i}.png"
        Image.fromarray(np.full((32, 32, 3), 128, dtype="uint8")).save(path)
        photos.append(
            PhotoMetadata(path=path, datetime_original=start + timedelta(seconds=offset), exif_source="exif")
        )
    return photos


def _scene_count(tab):
    return len({tab._table.item(r, 2).text() for r in range(tab._table.rowCount())})


def test_gap_starts_at_default(tab):
    assert tab.scene_gap_seconds == gui_bestcut_tab.DEFAULT_SCENE_GAP_SECONDS


def test_changing_gap_regroups_existing_preview_without_rescanning(tab, tmp_path):
    tab._photos = _photos(tmp_path, [0, 2, 10, 11])

    tab._gap_spin.setValue(3)
    tab._regroup()
    assert _scene_count(tab) == 2  # (0, 2) / (10, 11)
    assert sum(row.selected for row in tab._rows) == 2

    tab._gap_spin.setValue(10)
    tab._regroup()
    assert _scene_count(tab) == 1
    assert sum(row.selected for row in tab._rows) == 1
    assert "1개 장면" in tab._summary_text.toPlainText()


def test_gap_change_schedules_regroup_only_after_a_preview(tab, tmp_path):
    tab._gap_spin.setValue(5)
    assert not tab._regroup_timer.isActive()

    tab._photos = _photos(tmp_path, [0, 1])
    tab._gap_spin.setValue(6)
    assert tab._regroup_timer.isActive()


def test_gap_is_remembered_and_shown_in_grouping_help(app, tmp_path, monkeypatch):
    ini = str(tmp_path / "settings.ini")
    monkeypatch.setattr(
        gui_bestcut_tab,
        "make_settings",
        lambda: QtCore.QSettings(ini, QtCore.QSettings.Format.IniFormat),
    )
    first = gui_bestcut_tab.BestCutTab()
    first._gap_spin.setValue(8)

    second = gui_bestcut_tab.BestCutTab()

    assert second.scene_gap_seconds == 8
    assert "8초 이내" in second._grouping_help_label.toolTip()
    assert "8초 이내" in second._table.horizontalHeaderItem(2).toolTip()


def test_changing_input_folders_drops_the_regroup_source(tab, tmp_path):
    tab._photos = _photos(tmp_path, [0])

    tab._invalidate_preview()

    assert tab._photos is None


def test_preview_keeps_scan_result_and_regroups_if_gap_changed_meanwhile(tab, tmp_path):
    folder = tmp_path / "in"
    folder.mkdir()
    for i in range(3):
        Image.fromarray(np.full((32, 32, 3), 100 + i, dtype="uint8")).save(folder / f"IMG_{i}.jpg")
    worker = gui_bestcut_tab.PreviewWorker([folder], gap_seconds=tab.scene_gap_seconds)
    tab._preview_worker = worker
    worker.finished_ok.connect(tab._on_preview_done)
    tab._gap_spin.setValue(tab.scene_gap_seconds + 1)  # 미리보기 도중 간격 변경

    worker.run()

    assert [p.path.name for p in tab._photos] == ["IMG_0.jpg", "IMG_1.jpg", "IMG_2.jpg"]
    assert tab._regroup_timer.isActive()
