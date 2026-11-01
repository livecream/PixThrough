"""가중치 맞추기 창: 지금 결과(자동 선정 + 직접 넣기/빼기)로 자동 1등 컷이 내 선택과 얼마나
맞는지 보여주고, 더 잘 맞는 가중치를 추천한다(weight_tuning). 추천을 고르거나 직접 숫자를
바꿔 보면 일치율이 바로 바뀌고, "적용"을 눌러야 베스트컷 선정에 쓴다(앱 설정에 저장)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from gui_widgets import KEPT_ROW, compact, make_primary, paint_row
from report import ReportRow
from selector import DEFAULT_WEIGHTS, WEIGHT_KEYS, WEIGHT_LABELS, format_weights
from weight_tuning import GRID_STEP, Agreement, agreement, build_scenes, recommend


def describe(result: Agreement) -> str:
    if not result.total:
        return "셀 수 있는 장면 없음"
    text = f"장면 {result.total}개 중 {result.matched}개 일치 ({result.rate:.0%})"
    if result.touched_total:
        text += f" · 직접 고친 장면 {result.touched_total}개 중 {result.touched_matched}개"
    return text


class WeightTuningDialog(QDialog):
    def __init__(self, rows: list[ReportRow], current: dict[str, float], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("가중치 맞추기 — 내 선택에 맞춰보기")
        self.setMinimumWidth(620)
        self.chosen: dict[str, float] | None = None
        self._scenes = build_scenes(rows)
        now, candidates = recommend(rows, current)
        self._candidates = candidates

        heading = QLabel(f"<b>지금 가중치 {format_weights(current)}</b> — {describe(now)}")
        heading.setWordWrap(True)
        explain = QLabel(
            "장면마다 자동으로 1등 한 컷이 내가 최종으로 남긴 컷(자동 + 직접 넣기·빼기) 안에 있으면 "
            "일치로 셉니다. 손대지 않은 장면은 그대로 인정한 것으로 봅니다."
        )
        explain.setWordWrap(True)
        explain.setEnabled(False)
        layout = QVBoxLayout()
        layout.addWidget(heading)
        layout.addWidget(explain)

        if not now.touched_total:
            notice = QLabel(
                "아직 직접 넣거나 뺀 컷이 없어 자동 선택과 비교할 게 없습니다. 마음에 들지 않는 장면의 "
                "컷을 직접 넣고 빼 본 뒤 다시 열어 보세요. 아래에서 숫자를 바꿔 볼 수는 있습니다."
            )
            notice.setWordWrap(True)
            layout.addWidget(notice)
        elif now.touched_total < 5:
            notice = QLabel(f"직접 고친 장면이 {now.touched_total}개뿐이라 추천이 우연일 수 있습니다 — 여러 촬영에서 확인해 보세요.")
            notice.setWordWrap(True)
            layout.addWidget(notice)

        layout.addWidget(QLabel("추천 (일치가 많은 순, 같으면 지금 가중치에 가까운 순) — 눌러서 아래에 넣기"))
        self._table = QTableWidget(len(candidates), 3)
        self._table.setHorizontalHeaderLabels(("가중치", "일치", "직접 고친 장면"))
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.verticalHeader().setVisible(False)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        for i, candidate in enumerate(candidates):
            result = candidate.agreement
            values = (
                format_weights(candidate.weights),
                f"{result.matched} / {result.total} ({result.rate:.0%})" if result.total else "—",
                f"{result.touched_matched} / {result.touched_total}" if result.touched_total else "—",
            )
            for column, text in enumerate(values):
                self._table.setItem(i, column, QTableWidgetItem(text))
            if result.matched > now.matched:
                paint_row(self._table, i, KEPT_ROW)
        self._table.setMaximumHeight(self._table.sizeHintForRow(0) * (len(candidates) + 1) + 8 if candidates else 60)
        self._table.itemSelectionChanged.connect(self._on_candidate_selected)
        layout.addWidget(self._table)

        # 직접 정하기(합이 100이 아니어도 비율로 계산)
        self._spins: dict[str, QSpinBox] = {}
        spin_row = QHBoxLayout()
        spin_row.addWidget(QLabel("직접 정하기:"))
        for key in WEIGHT_KEYS:
            spin = QSpinBox()
            spin.setRange(0, 100)
            spin.setSingleStep(GRID_STEP)
            spin.setSuffix(" %")
            spin.setValue(round(current[key] * 100))
            spin.valueChanged.connect(self._update_trial)
            self._spins[key] = spin
            spin_row.addWidget(QLabel(WEIGHT_LABELS[key]))
            spin_row.addWidget(spin)
        spin_row.addStretch(1)
        layout.addLayout(spin_row)
        self._trial = QLabel()
        self._trial.setWordWrap(True)
        layout.addWidget(self._trial)

        default_button = compact(QPushButton("기본값(선명도 50·노출 30·눈뜸 20)으로"))
        default_button.clicked.connect(lambda: self._set_spins(DEFAULT_WEIGHTS))
        self._apply_button = make_primary(QPushButton("적용"))
        self._apply_button.setToolTip(
            "<p>이 가중치를 이 컴퓨터의 앱 설정에 저장하고 베스트컷 선정에 씁니다. 직접 넣거나 뺀 컷은 "
            "그대로이고, 언제든 기본값으로 되돌릴 수 있습니다.</p>"
        )
        self._apply_button.clicked.connect(self._apply)
        close_button = compact(QPushButton("닫기"))
        close_button.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addWidget(default_button)
        buttons.addStretch(1)
        buttons.addWidget(close_button)
        buttons.addWidget(self._apply_button)
        layout.addLayout(buttons)
        self.setLayout(layout)
        self._update_trial()

    def trial_weights(self) -> dict[str, float] | None:
        total = sum(spin.value() for spin in self._spins.values())
        if not total or not (self._spins["sharpness"].value() or self._spins["exposure"].value()):
            return None  # 눈뜸만으로는 얼굴 없는 사진의 점수를 낼 수 없다
        return {key: spin.value() / total for key, spin in self._spins.items()}

    def _set_spins(self, weights: dict[str, float]) -> None:
        for key, spin in self._spins.items():
            spin.blockSignals(True)
            spin.setValue(round(weights[key] * 100))
            spin.blockSignals(False)
        self._update_trial()

    def _on_candidate_selected(self) -> None:
        rows = self._table.selectionModel().selectedRows()
        if rows:
            self._set_spins(self._candidates[rows[0].row()].weights)

    def _update_trial(self) -> None:
        weights = self.trial_weights()
        if weights is None:
            self._trial.setText("선명도나 노출 중 하나는 0보다 커야 합니다.")
            self._apply_button.setEnabled(False)
            return
        total = sum(spin.value() for spin in self._spins.values())
        note = f" (합 {total}% — 비율로 계산: {format_weights(weights)})" if total != 100 else ""
        self._trial.setText(f"이 가중치로: {describe(agreement(self._scenes, weights))}{note}")
        self._apply_button.setEnabled(True)

    def _apply(self) -> None:
        self.chosen = self.trial_weights()
        if self.chosen is not None:
            self.accept()


def weights_differ(a: dict[str, float], b: dict[str, float]) -> bool:
    return any(abs(a[k] - b[k]) > 1e-6 for k in WEIGHT_KEYS)

