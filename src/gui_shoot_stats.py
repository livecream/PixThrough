"""촬영 통계 창: shoot_stats 결과를 표와 막대로 보여주고, 글로 복사할 수 있다."""

from __future__ import annotations

import html

from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QLabel, QPushButton, QTextBrowser, QVBoxLayout

from shoot_stats import Stats, as_text

_BAR_WIDTH = 220


def stats_html(stats: Stats) -> str:
    parts = []
    head = f"<b>사진 {stats.total}장</b>"
    if stats.first:
        head += f" · {stats.first:%Y-%m-%d %H:%M} ~ {stats.last:%Y-%m-%d %H:%M}"
    if stats.unknown_time:
        head += f" · 촬영 시각 정보 없음 {stats.unknown_time}장"
    parts.append(f"<p>{head}</p>")
    for section in stats.sections:
        total = sum(b.count for b in section.buckets) or 1
        biggest = max((b.count for b in section.buckets), default=1) or 1
        rows = []
        for bucket in section.buckets:
            width = max(2, int(_BAR_WIDTH * bucket.count / biggest))
            rows.append(
                "<tr>"
                f"<td width='170' style='padding-right:10px'>{html.escape(bucket.label)}</td>"
                f"<td width='{_BAR_WIDTH + 4}'><table cellspacing='0' cellpadding='0'><tr>"
                f"<td width='{width}' height='12' bgcolor='#4a90d9'></td></tr></table></td>"
                f"<td align='right' style='padding-left:8px'>{bucket.count}</td>"
                f"<td style='padding-left:6px; color:gray'>{bucket.count * 100 // total}%</td>"
                f"<td style='padding-left:10px'>{html.escape(bucket.extra)}</td>"
                "</tr>"
            )
        parts.append(f"<h3>{html.escape(section.title)}</h3><table cellspacing='2'>{''.join(rows)}</table>")
        if section.note:
            parts.append(f"<p style='color:gray'>※ {html.escape(section.note)}</p>")
    return "".join(parts)


class StatsDialog(QDialog):
    def __init__(self, stats: Stats, source: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("촬영 통계")
        self.resize(720, 640)
        self._stats = stats
        note = QLabel(f"{source} — 파일은 바꾸지 않습니다.")
        note.setWordWrap(True)
        note.setEnabled(False)
        view = QTextBrowser()
        view.setHtml(stats_html(stats))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        copy_button = QPushButton("글로 복사")
        copy_button.clicked.connect(lambda: QApplication.clipboard().setText(as_text(self._stats)))
        buttons.addButton(copy_button, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout()
        layout.addWidget(note)
        layout.addWidget(view, stretch=1)
        layout.addWidget(buttons)
        self.setLayout(layout)
