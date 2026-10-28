from pathlib import Path

import gui_reveal


def test_reveal_command_selects_file_on_macos_and_windows(monkeypatch):
    path = Path("/photos/IMG_0001.jpg")
    monkeypatch.setattr(gui_reveal.sys, "platform", "darwin")
    assert gui_reveal.reveal_command(path) == ["open", "-R", str(path)]
    monkeypatch.setattr(gui_reveal.sys, "platform", "win32")
    assert gui_reveal.reveal_command(path) == ["explorer", f"/select,{path}"]
    monkeypatch.setattr(gui_reveal.sys, "platform", "linux")
    assert gui_reveal.reveal_command(path) is None
