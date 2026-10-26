"""사진 선별 탭 컨테이너: 베스트컷 선별(M8)과 C컷 정리(M10)를 하위 탭으로 묶는다.

두 탭은 앱을 꺼도 남는 점수 저장소(score_store)를 함께 쓰므로, 저장된 점수를 지우는
버튼은 이 컨테이너에 하나만 두고 지운 뒤 두 탭의 미리보기를 함께 초기화한다.
입력 폴더도 두 탭 위에 하나만 두어 같이 쓴다(gui_input_folders.py).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QPushButton, QTabWidget, QVBoxLayout, QWidget

import score_store
from gui_badcut_tab import BadCutTab
from gui_bestcut_tab import BestCutTab
from gui_dnd import extract_dropped_folders
from gui_input_folders import InputFolderPanel


class PhotoSelectionTab(QWidget):
    def __init__(self) -> None:
        super().__init__()

        self.inputs = InputFolderPanel("입력 폴더 (베스트컷 선별·C컷 정리 공통)")
        self._best_cut_tab = BestCutTab(self.inputs)
        self._bad_cut_tab = BadCutTab(self.inputs)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._best_cut_tab, "베스트컷 선별")
        self.tabs.addTab(self._bad_cut_tab, "C컷 정리")
        self.setAcceptDrops(True)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 6, 0, 0)
        layout.addWidget(self.inputs)
        layout.addWidget(self.tabs, stretch=1)
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
        self.tabs.setCornerWidget(clear_button, Qt.Corner.TopRightCorner)

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
        return self._best_cut_tab.is_busy or self._bad_cut_tab.is_busy

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
        self._bad_cut_tab._invalidate_preview()
