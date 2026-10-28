"""작업 흐름 이어가기: 한 화면의 작업이 끝나면 "다음 단계" 버튼을 보여주고, 누르면 결과 폴더를
채운 채 다음 화면으로 넘어간다(시간순 정렬 → 베스트컷 선별 → 고객 셀렉·사진 축소 등).

화면들은 서로를 모르고 navigator()에 요청만 보낸다. 메인 창이 그 요청을 받아 해당 화면의
receive_folders를 부르고 메뉴를 옮긴다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton

from gui_widgets import compact

# 화면 이름 → 다음 단계 버튼 글자
PAGE_LABELS = {
    "rename": "시간순 정렬",
    "organize": "폴더 정리",
    "resize": "사진 축소",
    "proof": "고객 셀렉 페이지 만들기",
    "selection": "베스트컷·C컷 선별",
}


class Navigator(QObject):
    requested = Signal(str, object)  # 화면 이름, 넘길 폴더 목록(list[Path])

    def go(self, page: str, folders: list[Path]) -> None:
        self.requested.emit(page, list(folders))


_navigator: Navigator | None = None


def navigator() -> Navigator:
    global _navigator
    if _navigator is None:
        _navigator = Navigator()
    return _navigator


@dataclass(frozen=True)
class NextStep:
    page: str
    folders: tuple[Path, ...]
    hint: str = ""  # 버튼 툴팁(무엇이 넘어가는지)


class NextStepBar(QFrame):
    """작업이 끝난 뒤 결과 위에 나오는 "다음 단계 →" 버튼 줄. 비어 있으면 숨는다."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("nextStepBar")
        self.setStyleSheet(
            "#nextStepBar { background: rgba(66, 133, 244, 28); border-radius: 6px; }"
        )
        self._layout = QHBoxLayout()
        self._layout.setContentsMargins(8, 4, 8, 4)
        self._label = QLabel("다음 단계:")
        self._layout.addWidget(self._label)
        self._layout.addStretch(1)
        self.setLayout(self._layout)
        self.buttons: list[QPushButton] = []
        self.set_steps([])

    def set_steps(self, steps: list[NextStep], label: str = "다음 단계:") -> None:
        for button in self.buttons:
            self._layout.removeWidget(button)
            button.deleteLater()
        self.buttons = []
        self._label.setText(label)
        for step in steps:
            folders = [Path(f) for f in step.folders]
            button = compact(QPushButton(f"→ {PAGE_LABELS[step.page]}"))
            button.setToolTip(
                step.hint or "이 폴더를 넣은 채로 넘어갑니다:\n" + "\n".join(str(f) for f in folders)
            )
            button.clicked.connect(lambda _=False, p=step.page, f=folders: navigator().go(p, f))
            self._layout.insertWidget(self._layout.count() - 1, button)
            self.buttons.append(button)
        self.setVisible(bool(steps))

    def clear(self) -> None:
        self.set_steps([])
