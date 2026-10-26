"""사진 선별 묶음: 입력 폴더 칸(gui_input_folders.py) 아래에 베스트컷·C컷을 한 화면에서
고르는 화면(gui_bestcut_tab.BestCutTab)을 둔다. 예전에는 베스트컷 선별(M8)과 C컷 정리(M10)가
따로 된 화면이었다.

저장된 점수를 지우는 버튼도 여기(입력 폴더 칸 오른쪽)에 둔다.
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QMessageBox, QPushButton, QStackedWidget, QVBoxLayout, QWidget

import score_store
from gui_bestcut_tab import BestCutTab
from gui_dnd import extract_dropped_folders
from gui_input_folders import InputFolderPanel


class PhotoSelectionTab(QWidget):
    """입력 폴더 칸 + 사진 선별 화면. 왼쪽 메뉴가 show_page를 부른다(화면은 하나)."""

    # 베스트컷 선별과 C컷 정리는 한 화면(BestCutTab)으로 합쳤다. 예전 메뉴 이름도 받아 준다.
    PAGES = ("selection",)
    LEGACY_PAGES = ("bestcut", "badcut")

    def __init__(self) -> None:
        super().__init__()

        self.inputs = InputFolderPanel("입력 폴더")
        self._best_cut_tab = BestCutTab(self.inputs)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._best_cut_tab)
        self.setAcceptDrops(True)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        # 아래 화면들의 안쪽 여백과 왼쪽 끝을 맞춘다
        inputs_row = QHBoxLayout()
        inputs_row.setContentsMargins(9, 0, 9, 0)
        inputs_row.addWidget(self.inputs)
        layout.addLayout(inputs_row)
        layout.addWidget(self.pages, stretch=1)
        self.setLayout(layout)

        clear_button = QPushButton("저장된 점수 지우기")
        clear_button.setToolTip(
            "<p>한 번 채점한 사진의 점수와 유사도 지문은 앱을 꺼도 남아 있어, 다음에 같은 "
            "사진을 열면 바로 결과가 나옵니다. 사진 파일이 바뀌면 그 사진만 자동으로 다시 "
            "계산하므로 보통은 지울 필요가 없습니다.</p>"
            "<p>지우면 다음 미리보기 때 모든 사진을 처음부터 다시 계산합니다. 원본 사진은 "
            "건드리지 않습니다.</p>"
        )
        clear_button.clicked.connect(self._clear_saved_scores)
        self.inputs.add_side_widget(clear_button)

    def show_page(self, name: str) -> None:
        self.pages.setCurrentIndex(0)

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt override signature
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt override signature
        folders = extract_dropped_folders(event.mimeData())
        if folders:
            self.inputs.add_folders(folders)
        event.acceptProposedAction()

    @property
    def is_busy(self) -> bool:
        return self._best_cut_tab.is_busy

    def _clear_saved_scores(self) -> None:
        if self.is_busy:
            QMessageBox.warning(
                self, "작업 진행 중", "미리보기/적용이 끝난 뒤에 지울 수 있습니다."
            )
            return
        confirm = QMessageBox.question(
            self,
            "저장된 점수 지우기",
            "저장된 점수와 유사도 지문을 모두 지울까요?\n다음 미리보기 때 모든 사진을 "
            "처음부터 다시 계산합니다. 원본 사진은 건드리지 않습니다.",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        score_store.clear()
        # 점수가 비었으니 지금 보이는 미리보기에서 간격·기준을 바꿔 다시 고르면 사진을 다시
        # 채점하느라 화면이 멈출 수 있다 — 두 탭 모두 미리보기를 새로 하도록 초기화한다.
        self._best_cut_tab._invalidate_preview()
