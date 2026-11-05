def test_saved_values_fall_back_to_defaults(tmp_path):
    from PySide6.QtCore import QSettings

    from gui_appearance import font_scale, theme

    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    assert font_scale(settings) == 100 and theme(settings) == "system"
    settings.setValue("appearance/font_scale", 115)
    settings.setValue("appearance/theme", "dark")
    assert font_scale(settings) == 115 and theme(settings) == "dark"
    settings.setValue("appearance/font_scale", "777")
    settings.setValue("appearance/theme", "neon")
    assert font_scale(settings) == 100 and theme(settings) == "system"
