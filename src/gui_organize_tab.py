"""M11: 폴더 정리 탭. RAW/영상 분리, 짝 안 맞는 파일 찾기, 촬영 날짜별 정리,
빈 폴더 정리를 폴더 하나 선택 후 버튼 한 번으로 수행한다.

버튼은 하는 일의 위험도대로 세 칸에 나눠 둔다: 살펴보기(파일을 바꾸지 않음) /
정리(파일 이동, 되돌리기 가능) / 삭제(확인 후 지움). 버튼마다 한 줄 설명을 붙인다.

무거운 스코어링(mediapipe)이 없는 단순 파일 이동/조회라 QThread 없이 동기로
처리한다.
ponytail: 폴더 안 파일이 매우 많으면(수만 개) 버튼 클릭 시 UI가 잠깐 멈출 수
있음 — 체감되면 다른 탭처럼 QThread로 옮길 것.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui_dnd import extract_dropped_folders
from gui_format import format_duplicate_report, format_folder_summary
from gui_settings import load_folder, make_settings, save_folder
from gui_widgets import PathLabel, compact, make_danger
from organize import (
    apply_move_plan,
    build_capture_date_plan,
    build_move_by_extension_plan,
    build_undo_plan,
    delete_duplicate_files,
    find_duplicate_files,
    find_empty_folders,
    find_raw_jpg_mismatches,
    remove_empty_folders,
    summarize_folder,
)
from rename_by_time import VIDEO_EXTENSIONS
from scanner import RAW_EXTENSIONS

def _section(title: str, actions: list[tuple[QPushButton, str]]) -> QGroupBox:
    """제목 상자 안에 버튼과 그 아래 한 줄 설명을 차례로 쌓는다."""
    layout = QVBoxLayout()
    for button, description in actions:
        layout.addWidget(button)
        label = QLabel(description)
        label.setWordWrap(True)
        palette = label.palette()  # 설명은 흐린 글자색으로
        palette.setColor(QPalette.ColorRole.WindowText, palette.color(QPalette.ColorRole.PlaceholderText))
        label.setPalette(palette)
        layout.addWidget(label)
        layout.addSpacing(6)
    layout.addStretch()
    group = QGroupBox(title)
    group.setLayout(layout)
    return group


class OrganizeTab(QWidget):
    def __init__(self) -> None:
        super().__init__()

        self._folder: Path | None = None
        # 직전 정리 작업 하나만 기억해 "실행 취소"로 되돌린다. 앱을 껐다 켜면
        # 사라짐(세션 한정) — organize_log_*.jsonl에는 남아있어 수동 확인 가능.
        self._last_operation: dict | None = None
        self._duplicate_groups: list = []
        self.setAcceptDrops(True)
        self._settings = make_settings()

        self._folder_label = PathLabel("(선택 안 됨 — 폴더를 이 탭에 끌어다 놓아도 됩니다)")
        folder_button = compact(QPushButton("선택…"))
        folder_button.clicked.connect(self._choose_folder)

        summary_button = QPushButton("폴더 정보 보기")
        summary_button.clicked.connect(self._show_folder_summary)

        raw_button = QPushButton("RAW 폴더로 이동")
        raw_button.clicked.connect(self._move_raw)

        video_button = QPushButton("영상 폴더로 이동")
        video_button.clicked.connect(self._move_video)

        mismatch_button = QPushButton("RAW-JPG 짝 안 맞는 파일 찾기")
        mismatch_button.clicked.connect(self._find_mismatches)

        date_button = QPushButton("촬영 날짜별로 정리")
        date_button.clicked.connect(self._organize_by_date)

        empty_button = QPushButton("빈 폴더 정리")
        empty_button.clicked.connect(self._remove_empty_folders)

        find_duplicate_button = QPushButton("중복 파일 찾기")
        find_duplicate_button.clicked.connect(self._find_duplicates)

        self._delete_duplicate_button = QPushButton("중복 파일 삭제")
        make_danger(self._delete_duplicate_button)
        self._delete_duplicate_button.setEnabled(False)
        self._delete_duplicate_button.clicked.connect(self._delete_duplicates)

        make_danger(empty_button)

        self._undo_button = compact(QPushButton("방금 작업 실행 취소"))
        self._undo_button.setToolTip(
            "<p>마지막 이동(RAW·영상·날짜별 정리)이나 빈 폴더 정리를 되돌립니다. 중복 파일 "
            "삭제는 되돌릴 수 없습니다. 앱을 끄면 기억이 사라지지만, 폴더 안 "
            "organize_log_*.jsonl에 기록이 남습니다.</p>"
        )
        self._undo_button.setEnabled(False)
        self._undo_button.clicked.connect(self._undo_last_operation)

        self._result_text = QPlainTextEdit()
        self._result_text.setReadOnly(True)
        self._result_text.setAcceptDrops(False)
        self._result_text.setPlaceholderText("버튼을 누르면 결과가 여기에 나옵니다.")

        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("정리할 폴더:"))
        folder_row.addWidget(self._folder_label, stretch=1)
        folder_row.addWidget(folder_button)

        sections = QHBoxLayout()
        sections.addWidget(
            _section(
                "살펴보기 — 파일을 바꾸지 않음",
                [
                    (summary_button, "하위 폴더까지 파일 수·용량을 종류(사진·RAW·영상 등)별로 셉니다."),
                    (mismatch_button, "같은 이름의 짝이 없는 RAW나 JPG를 알려줍니다. 옮기거나 지우지 않습니다."),
                    (find_duplicate_button, "내용이 완전히 같은 파일을 하위 폴더까지 찾습니다."),
                ],
            ),
            stretch=1,
        )
        sections.addWidget(
            _section(
                "정리 — 파일 이동 (되돌리기 가능)",
                [
                    (raw_button, "맨 위 폴더의 RAW 파일을 RAW/ 하위 폴더로 옮깁니다."),
                    (video_button, "맨 위 폴더의 영상 파일을 영상/ 하위 폴더로 옮깁니다."),
                    (date_button, "맨 위 폴더의 사진·영상을 촬영 날짜(YYYY-MM-DD) 폴더로 옮깁니다."),
                ],
            ),
            stretch=1,
        )
        sections.addWidget(
            _section(
                "삭제 — 확인 후 지움",
                [
                    (empty_button, "파일이 하나도 없는 폴더를 하위까지 찾아 지웁니다. 되돌리기 가능."),
                    (
                        self._delete_duplicate_button,
                        "'중복 파일 찾기' 결과에서 그룹마다 1개만 남기고 나머지를 영구히 지웁니다. "
                        "되돌릴 수 없습니다.",
                    ),
                ],
            ),
            stretch=1,
        )

        result_header = QHBoxLayout()
        result_header.addWidget(QLabel("결과"))
        result_header.addStretch()
        result_header.addWidget(self._undo_button)

        layout = QVBoxLayout()
        layout.addLayout(folder_row)
        layout.addLayout(sections)
        layout.addLayout(result_header)
        layout.addWidget(self._result_text, stretch=1)

        restored_folder = load_folder(self._settings, "organize/folder")
        if restored_folder is not None:
            self._folder = restored_folder
            self._folder_label.set_path(restored_folder)

        self.setLayout(layout)

    @property
    def is_busy(self) -> bool:
        return False

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt override signature
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt override signature
        folders = extract_dropped_folders(event.mimeData())
        if folders:
            self._set_folder(folders[0])
        event.acceptProposedAction()

    def _choose_folder(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "정리할 폴더 선택")
        if directory:
            self._set_folder(Path(directory))

    def _set_folder(self, folder: Path) -> None:
        self._folder = folder
        self._folder_label.set_path(folder)
        self._result_text.setPlainText("")
        self._last_operation = None
        self._undo_button.setEnabled(False)
        self._duplicate_groups = []
        self._delete_duplicate_button.setEnabled(False)
        save_folder(self._settings, "organize/folder", folder)

    def _require_folder(self) -> Path | None:
        if self._folder is None or not self._folder.is_dir():
            QMessageBox.warning(self, "폴더 없음", "먼저 정리할 폴더를 선택하세요.")
            return None
        return self._folder

    def _confirm(self, title: str, message: str) -> bool:
        return (
            QMessageBox.question(self, title, message) == QMessageBox.StandardButton.Yes
        )

    def _remember_move(self, rows: list) -> None:
        self._last_operation = {"kind": "move", "rows": rows}
        self._undo_button.setEnabled(True)

    def _move_raw(self) -> None:
        folder = self._require_folder()
        if folder is None:
            return
        rows = build_move_by_extension_plan(folder, RAW_EXTENSIONS, "RAW")
        if not rows:
            self._result_text.setPlainText("이동할 RAW 파일이 없습니다.")
            return
        dest = folder / "RAW"
        if not self._confirm("RAW 이동 확인", f"{len(rows)}개 RAW 파일을 {dest}(으)로 이동합니다.\n계속하시겠습니까?"):
            return
        apply_move_plan(rows, log_folder=folder, action="move_raw")
        self._remember_move(rows)
        self._result_text.setPlainText(f"RAW {len(rows)}개 이동 완료 → {dest}")

    def _move_video(self) -> None:
        folder = self._require_folder()
        if folder is None:
            return
        rows = build_move_by_extension_plan(folder, VIDEO_EXTENSIONS, "영상")
        if not rows:
            self._result_text.setPlainText("이동할 영상 파일이 없습니다.")
            return
        dest = folder / "영상"
        if not self._confirm("영상 이동 확인", f"{len(rows)}개 영상 파일을 {dest}(으)로 이동합니다.\n계속하시겠습니까?"):
            return
        apply_move_plan(rows, log_folder=folder, action="move_video")
        self._remember_move(rows)
        self._result_text.setPlainText(f"영상 {len(rows)}개 이동 완료 → {dest}")

    def _find_mismatches(self) -> None:
        folder = self._require_folder()
        if folder is None:
            return
        messages = find_raw_jpg_mismatches(folder)
        if not messages:
            self._result_text.setPlainText("짝이 안 맞는 파일이 없습니다.")
            return
        self._result_text.setPlainText(f"짝 안 맞는 파일 {len(messages)}개:\n\n" + "\n".join(messages))

    def _organize_by_date(self) -> None:
        folder = self._require_folder()
        if folder is None:
            return
        rows = build_capture_date_plan(folder, video_extensions=VIDEO_EXTENSIONS)
        if not rows:
            self._result_text.setPlainText("정리할 파일이 없습니다.")
            return
        dest_folders = sorted({r.new_path.parent.name for r in rows})
        if not self._confirm(
            "날짜별 정리 확인",
            f"{len(rows)}개 파일을 다음 날짜별 폴더로 이동합니다:\n"
            + ", ".join(dest_folders)
            + "\n계속하시겠습니까?",
        ):
            return
        apply_move_plan(rows, log_folder=folder, action="organize_by_date")
        self._remember_move(rows)
        self._result_text.setPlainText(f"{len(rows)}개 파일을 날짜별 폴더로 이동 완료")

    def _remove_empty_folders(self) -> None:
        folder = self._require_folder()
        if folder is None:
            return
        empty = find_empty_folders(folder)
        if not empty:
            self._result_text.setPlainText("빈 폴더가 없습니다.")
            return
        listing = "\n".join(str(p) for p in empty)
        if not self._confirm(
            "빈 폴더 삭제 확인", f"다음 {len(empty)}개 빈 폴더를 삭제합니다:\n{listing}\n계속하시겠습니까?"
        ):
            return
        remove_empty_folders(empty, log_folder=folder)
        self._last_operation = {"kind": "empty_folders", "folders": empty}
        self._undo_button.setEnabled(True)
        self._result_text.setPlainText(f"빈 폴더 {len(empty)}개 삭제 완료")

    def _undo_last_operation(self) -> None:
        if self._last_operation is None:
            return
        kind = self._last_operation["kind"]
        if kind == "move":
            rows = self._last_operation["rows"]
            apply_move_plan(build_undo_plan(rows))
            self._result_text.setPlainText(f"{len(rows)}개 파일 되돌리기 완료")
        elif kind == "empty_folders":
            folders = self._last_operation["folders"]
            for folder in folders:
                folder.mkdir(parents=True, exist_ok=True)
            self._result_text.setPlainText(f"빈 폴더 {len(folders)}개 다시 생성(복구) 완료")
        self._last_operation = None
        self._undo_button.setEnabled(False)

    def _show_folder_summary(self) -> None:
        folder = self._require_folder()
        if folder is None:
            return
        summary = summarize_folder(folder, video_extensions=VIDEO_EXTENSIONS)
        self._result_text.setPlainText(format_folder_summary(summary))

    def _find_duplicates(self) -> None:
        folder = self._require_folder()
        if folder is None:
            return
        groups = find_duplicate_files(folder)
        self._duplicate_groups = groups
        self._delete_duplicate_button.setEnabled(bool(groups))
        if not groups:
            self._result_text.setPlainText("중복 파일이 없습니다.")
            return
        self._result_text.setPlainText(format_duplicate_report(groups))

    def _delete_duplicates(self) -> None:
        if not self._duplicate_groups:
            return
        folder = self._require_folder()
        if folder is None:
            return
        candidates = [dup for group in self._duplicate_groups for dup in group.duplicates]
        listing = "\n".join(str(p) for p in candidates)
        confirmed = self._confirm(
            "⚠️ 중복 파일 삭제 확인",
            f"다음 {len(candidates)}개 파일을 영구히 삭제합니다 (복구 불가):\n{listing}\n\n"
            "그룹마다 1개(보존 대상)는 남으므로 사진 자체가 사라지지는 않지만, "
            "삭제된 파일은 휴지통을 거치지 않고 즉시 사라집니다.\n계속하시겠습니까?",
        )
        if not confirmed:
            return
        deleted_count = delete_duplicate_files(self._duplicate_groups, log_folder=folder)
        self._duplicate_groups = []
        self._delete_duplicate_button.setEnabled(False)
        self._result_text.setPlainText(f"중복 파일 {deleted_count}개 삭제 완료")
