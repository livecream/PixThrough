"""보기 설정(편의 7, 사용자 결정): 앱 전체 글자 크기 작게·보통·크게·아주 크게(90·100·115·130%)와 테마
(시스템·라이트·다크). 메뉴 "보기"에서 고르면 바로 바꾸고 QSettings `appearance/*`에 기억한다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QSettings, Qt

if TYPE_CHECKING:  # QtGui·QtWidgets는 쓸 때 읽는다(화면 없는 테스트 환경 — libEGL 없음)
    from PySide6.QtWidgets import QApplication

FONT_SCALE_KEY = "appearance/font_scale"
THEME_KEY = "appearance/theme"
FONT_SCALES = ((90, "작게"), (100, "보통"), (115, "크게"), (130, "아주 크게"))
THEMES = (("system", "시스템 설정 따르기"), ("light", "라이트"), ("dark", "다크"))
_BASE_PROPERTY = "pixthrough_base_point_size"


def font_scale(settings: QSettings) -> int:
    try:
        value = int(float(settings.value(FONT_SCALE_KEY, 100)))
    except (TypeError, ValueError):
        return 100
    return value if value in dict(FONT_SCALES) else 100


def theme(settings: QSettings) -> str:
    value = str(settings.value(THEME_KEY, "system") or "system")
    return value if value in dict(THEMES) else "system"


def apply_font_scale(app: "QApplication", percent: int) -> None:
    """앱 기본 글자 크기를 처음 크기의 percent%로(여러 번 불러도 처음 크기 기준)."""
    from PySide6.QtGui import QFont

    base = app.property(_BASE_PROPERTY)
    if base is None:
        base = app.font().pointSizeF()
        app.setProperty(_BASE_PROPERTY, base)
    font = QFont(app.font())
    font.setPointSizeF(max(6.0, float(base) * percent / 100))
    app.setFont(font)


def apply_theme(app: "QApplication", name: str) -> bool:
    """테마를 바꾼다. 이 Qt가 지원하지 않으면(6.8 미만) False."""
    hints = app.styleHints()
    if not hasattr(hints, "setColorScheme"):
        return False
    scheme = {"light": Qt.ColorScheme.Light, "dark": Qt.ColorScheme.Dark}.get(name, Qt.ColorScheme.Unknown)
    hints.setColorScheme(scheme)
    return True


def apply_saved(app: "QApplication", settings: QSettings) -> None:
    apply_font_scale(app, font_scale(settings))
    apply_theme(app, theme(settings))
