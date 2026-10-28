"""도움말 창 두 가지: 화면별 단축키 모음(도움말 > 단축키, ? 또는 F1)과 처음 켰을 때 보여주는
사용 순서 안내(도움말 > 처음 사용 안내, 처음 한 번만 자동으로).

단축키 목록은 각 화면이 실제로 등록한 키(gui_bestcut_tab·gui_scene_gallery·gui_photo_viewer·
gui_compare_view)와 맞춰 둔다 — 키를 바꾸면 여기도 고칠 것.
"""

from __future__ import annotations

import html

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

from gui_settings import load_bool, make_settings, save_bool
from gui_widgets import compact, make_primary

WELCOME_SHOWN_KEY = "help/welcome_shown"

# (어디서, [(키, 하는 일)])
SHORTCUTS: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "베스트컷·C컷 선별 — 표·갤러리",
        [
            ("Space", "고른 사진을 선택에 넣기 / 빼기"),
            ("P", "선택에 넣기"),
            ("X", "선택에서 빼기"),
            ("Enter 또는 더블클릭", "크게 보기"),
            ("C", "같은 장면 컷 나란히 비교"),
            ("S", "이 사진부터 새 장면으로 나누기"),
            ("M", "이 사진을 앞 장면과 합치기"),
            ("← / →", "앞·뒤 사진 (갤러리)"),
            ("↑ / ↓", "앞·뒤 장면 (갤러리)"),
            ("오른쪽 클릭", "넣기/빼기·크게 보기·비교·나누기·합치기 메뉴"),
        ],
    ),
    (
        "크게 보기 창",
        [
            ("← / →", "같은 장면의 앞·뒤 사진"),
            ("Space", "선택에 넣기 / 빼기"),
            ("P / X", "선택에 넣기 / 빼기"),
        ],
    ),
    (
        "나란히 비교 창",
        [
            ("1 ~ 4 또는 칸 클릭", "그 칸 고르기"),
            ("← / →", "옆 칸으로 (끝에서는 다음 컷을 밀어 봄)"),
            ("Space / P / X", "고른 칸을 넣기·빼기 / 넣기 / 빼기"),
            ("휠", "커서 자리 기준으로 확대·축소 (모든 칸 함께)"),
            ("끌기", "확대한 자리 옮기기 (모든 칸 함께)"),
            ("더블클릭 또는 F", "그 자리 100% ↔ 맞춤"),
        ],
    ),
    (
        "어디서나",
        [
            ("? 또는 F1", "이 단축키 모음"),
            ("폴더 끌어다 놓기", "그 화면의 입력 폴더로"),
        ],
    ),
]


def shortcuts_html() -> str:
    parts = []
    for title, keys in SHORTCUTS:
        parts.append(f"<h3>{html.escape(title)}</h3><table cellspacing='0' cellpadding='4'>")
        for key, description in keys:
            parts.append(
                f"<tr><td style='padding-right:18px; white-space:nowrap'><b>{html.escape(key)}</b></td>"
                f"<td>{html.escape(description)}</td></tr>"
            )
        parts.append("</table>")
    return "".join(parts)


class ShortcutsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("단축키")
        self.resize(520, 600)
        view = QTextBrowser()
        view.setHtml(shortcuts_html())
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout()
        layout.addWidget(view)
        layout.addWidget(buttons)
        self.setLayout(layout)


# (화면 이름, 메뉴 위치, 하는 일)
WORKFLOW = [
    ("rename", "사진 파일 정리 › 시간순 정렬", "여러 카메라(바디)로 찍은 사진·영상을 촬영 시각 순서로 00001부터 번호를 붙입니다."),
    ("selection", "사진 선별 › 베스트컷·C컷 선별", "비슷한 순간끼리 장면으로 묶고 장면마다 가장 좋은 컷을 골라 복사합니다. 흐리거나 눈 감은 컷은 따로 걸러 냅니다."),
    ("proof", "사진 파일 정리 › 고객 셀렉", "고른 컷으로 고객이 브라우저에서 고르는 페이지를 만들고, 고객이 고른 결과대로 원본을 모읍니다."),
    ("resize", "사진 파일 정리 › 사진 축소", "보내기 좋게 사진의 긴 변을 줄여 JPG로 저장합니다."),
    ("organize", "사진 파일 정리 › 폴더 정리", "언제든: RAW·영상 나누기, 날짜별 정리, 중복 파일·빈 폴더 정리."),
]

_SAFETY = (
    "원본은 안전하게: 모든 작업은 미리보기(표)로 먼저 보여주고, 버튼을 눌러야 실행합니다. "
    "베스트컷은 기본이 복사이고, 이동·정리는 \"↶ 되돌리기\"가 있습니다."
)


class WelcomeDialog(QDialog):
    """처음 켰을 때 보여주는 사용 순서. 단계마다 "이 화면으로" 버튼."""

    page_requested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("PixThrough 처음 사용 안내")
        heading = QLabel("PixThrough는 보통 이 순서로 씁니다")
        font = heading.font()
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() * 1.25)
        heading.setFont(font)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        for row, (page, where, what) in enumerate(WORKFLOW):
            number = QLabel(f"{row + 1}")
            number.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
            number_font = number.font()
            number_font.setBold(True)
            number.setFont(number_font)
            text = QLabel(f"<b>{html.escape(where)}</b><br>{html.escape(what)}")
            text.setWordWrap(True)
            go = compact(QPushButton("이 화면으로"))
            go.clicked.connect(lambda _=False, p=page: self._go(p))
            grid.addWidget(number, row, 0)
            grid.addWidget(text, row, 1)
            grid.addWidget(go, row, 2, alignment=Qt.AlignmentFlag.AlignTop)
        grid.setColumnStretch(1, 1)

        tips = QLabel(
            f"{html.escape(_SAFETY)}<br><br>"
            "작업이 끝나면 결과 위에 <b>다음 단계</b> 버튼이 나와, 결과 폴더를 넣은 채로 다음 화면으로 넘어갑니다. "
            "단축키는 <b>?</b> 키나 <b>도움말 › 단축키</b>에서 볼 수 있습니다."
        )
        tips.setWordWrap(True)

        self.show_again = QCheckBox("다음에 켤 때도 이 안내 보기")
        close = make_primary(QPushButton("시작하기"))
        close.clicked.connect(self.accept)
        bottom = QHBoxLayout()
        bottom.addWidget(self.show_again, stretch=1)
        bottom.addWidget(close)

        layout = QVBoxLayout()
        layout.addWidget(heading)
        layout.addSpacing(6)
        layout.addLayout(grid)
        layout.addSpacing(10)
        layout.addWidget(tips)
        layout.addSpacing(6)
        layout.addLayout(bottom)
        self.setLayout(layout)
        self.setMinimumWidth(640)

    def _go(self, page: str) -> None:
        self.page_requested.emit(page)
        self.accept()


def should_show_welcome() -> bool:
    return not load_bool(make_settings(), WELCOME_SHOWN_KEY, False)


def remember_welcome(show_again: bool) -> None:
    save_bool(make_settings(), WELCOME_SHOWN_KEY, not show_again)
