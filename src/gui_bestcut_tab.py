"""M8+M10: 사진 선별 화면 — 베스트컷(A컷) 고르기와 C컷(흔들림·눈감음) 거르기를 한 화면에서.

미리보기 한 번으로 사진마다 세 가지 판정을 보여준다: 베스트컷(남김) / 보통(장면에서 밀림) /
C컷(기준보다 흐리거나 눈 감음). C컷 기준(선명도·눈뜸)은 기본값 없이 사용자가 정하고(0이면
꺼짐), 기준을 정하면 C컷은 베스트컷 후보에서 빠진다(사용자 결정). 적용은 위험도가 달라
"베스트컷 복사(또는 이동)"와 "C컷 이동" 두 버튼으로 나눈다. 예전에는 베스트컷 선별과 C컷
정리가 따로 된 화면이었다.

장면 간격(몇 초 이내면 같은 장면인지)은 화면에서 조절한다(PROJECT_SPEC.md 4.2절
"N은 설정 가능한 값"). 미리보기로 한 번 스캔·스코어링해두면, 간격을 바꿀 때마다
사진을 다시 읽지 않고 캐시된 점수로 즉시 다시 묶는다.

"비슷하지 않은 사진 나누기"(기본 꺼짐)를 켜면, 시간상 같은 장면이어도 바로 앞 사진과
이미지 지문(pHash) 차이가 사용자가 정한 값을 넘는 곳에서 장면을 나눈다. 지문은 미리보기
때 항상 계산해두므로, 켜고 끄거나 기준을 바꿔도 즉시 다시 묶인다. 기준을 고르는 데
참고하도록 표에 "앞 사진과 차이" 칸을 보여준다.

장면을 어떻게 묶고 어떤 점수로 고르는지가 화면에 드러나지 않으면 결과를 믿고
쓰기 어려워, 설명 툴팁(ⓘ)과 사진별 점수 칸을 함께 보여준다.

자동 선정(장면당 1장)이 마음에 들지 않으면 사용자가 컷을 직접 더 넣거나 뺄 수 있다.
직접 고른 것은 경로별로 따로 들고 있다가 자동 결과에 덮어쓰므로(report.
apply_manual_choices), 장면 간격을 바꿔 다시 묶어도 유지된다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import NamedTuple

from PySide6.QtCore import QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QButtonGroup,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from file_ops import ApplyError, FileOpResult, UndoOutcome, apply_selection, undo_moves
from grouping import (
    DEFAULT_SCENE_GAP_SECONDS,
    JOIN,
    SPLIT,
    apply_scene_edits,
    distances_to_previous,
    group_by_time_gap,
    split_dissimilar,
)
import app_log
from clock_sync import shift_photos
from gui_clock_sync import ClockSyncDialog, describe_offsets, offset_store
from gui_dnd import extract_dropped_folders
from gui_format import (
    bestcut_grouping_help,
    bestcut_scoring_help,
    format_apply_summary,
    format_badcut_summary,
    format_manual_choice_summary,
    format_preview_header,
    format_sharpness_region,
    format_threshold_reference,
    sharpness_measure_help,
)
from gui_settings import (
    load_bool,
    load_float,
    load_folder,
    make_settings,
    save_bool,
    save_float,
    save_folder,
)
from gui_compare_view import STATE_FLAGGED, STATE_KEPT, STATE_NONE, CompareDialog
from gui_photo_viewer import PhotoViewerDialog, ViewerAction, ViewerShortcut
import choice_store
import score_store
from gui_worker import CancellableWorker, PreviewCancelled
from image_hash import HASH_BITS, hashes_for
from badcut import BadCutThresholds, badcut_reason
from report import ReportRow, apply_manual_choices, build_report, write_apply_report
from scanner import PhotoMetadata, scan_and_extract_many
from parallel import map_in_order
from scoring import score_photos
from thumbnails import make_thumbnail_jpeg
from xmp_rating import (
    ACTION_CREATED,
    ACTION_SKIPPED,
    ACTION_UPDATED,
    RATING_MAX,
    RATING_MIN,
    XmpPlanRow,
    XmpResult,
    default_run_dir,
    plan_xmp,
    undo_xmp,
    write_xmp,
)
from gui_scene_gallery import THUMB as GALLERY_THUMB, GalleryPhoto, GalleryScene, SceneGallery
from gui_input_folders import InputFolderPanel
from gui_navigation import NextStep, NextStepBar
from gui_widgets import (
    EtaProgressBar,
    BAR_ROLE,
    FLAGGED_ROW,
    KEPT_ROW,
    SCENE_BAND,
    BarDelegate,
    GuidePanel,
    PathLabel,
    SummaryLabel,
    compact,
    fit_text,
    make_danger,
    make_primary,
    paint_row,
    status_row,
)

log = app_log.get("bestcut")


_TOTAL_TOOLTIP = (
    "<p><b>종합점수(0~1)</b>: 선명도·노출·눈뜸을 비율대로 합친 값입니다. "
    "장면 안에서 이 값이 가장 높은 컷이 선택됩니다.</p>"
)
_SHARPNESS_TOOLTIP = (
    "<p><b>선명도</b>: 같은 장면 안에서 몇 번째로 또렷한지(1위 = 가장 선명)입니다. "
    "선명도 원래 값은 해상도·피사체마다 범위가 달라 장면끼리 비교할 수 없어 순위로 "
    "보여주고, 원래 값은 칸에 마우스를 올리면 나옵니다. 막대는 장면 안에서 가장 흐린 "
    "컷(빈 막대)~가장 선명한 컷(꽉 찬 막대) 사이 위치로, 종합점수에 이 값이 반영됩니다."
    "</p>" + sharpness_measure_help()
)
_BADCUT_SHARPNESS_TOOLTIP = (
    "<p><b>선명도 기준(C컷: 블러)</b>: 이보다 선명도 원래 값이 낮은 사진을 C컷으로 봅니다. "
    "0이면 블러 판정을 하지 않습니다.</p>"
    "<p>원래 값은 해상도·피사체마다 범위가 달라 숫자로 정하기 어려우니, 표의 '선명도' "
    "머리글을 눌러 정렬하고(원래 값 순서) 사진을 크게 보면서 '이 정도까지는 쓸 만하다' 싶은 "
    "사진을 고른 뒤 <b>[선명도 기준]</b>을 누르세요. 그 사진보다 흐린 "
    "사진이 C컷이 되고, 기준 사진 자체는 남습니다.</p>" + sharpness_measure_help()
)
_BADCUT_EYES_TOOLTIP = (
    "<p><b>눈뜸 기준(C컷: 눈감음)</b>: 이보다 눈이 더 감긴 사진을 C컷으로 봅니다(0~1, "
    "0이면 판정 안 함). 얼굴이 검출되지 않았거나 눈을 분석하지 못한 사진은 빠집니다.</p>"
    "<p>'눈뜸'으로 정렬해 크게 보면서 '이 정도 감은 건 괜찮다' 싶은 사진을 고른 뒤 "
    "<b>[눈뜸 기준]</b>을 누르세요.</p>"
)
_MANUAL_SPIN_TOOLTIP = (
    "<p>숫자를 직접 넣어 미세 조정할 수도 있습니다. 직접 바꾸면 기준 사진 지정은 풀리고 "
    "입력한 값이 그대로 기준이 됩니다. 바꾸면 다시 스캔하지 않고 즉시 다시 판정합니다.</p>"
)
_EXPOSURE_TOOLTIP = (
    "<p><b>노출(0~1)</b>: 완전히 까맣게 뭉개지거나 하얗게 날아간 픽셀이 적을수록 "
    "1에 가깝습니다.</p>"
)
_EYES_TOOLTIP = (
    "<p><b>눈뜸(0~1)</b>: 얼굴이 검출된 사진에서 눈을 뜬 정도입니다. 빈칸은 얼굴이 "
    "검출되지 않았거나 눈을 분석하지 못한(옆모습 등) 사진으로, 종합점수에서 이 항목을 "
    "빼고 계산합니다.</p>"
)


SCENE_GAP_MIN_SECONDS = 0.5
SCENE_GAP_MAX_SECONDS = 60.0
SCENE_GAP_SETTINGS_KEY = "bestcut/scene_gap_seconds"
SPLIT_ENABLED_SETTINGS_KEY = "bestcut/split_dissimilar"
SPLIT_DISTANCE_SETTINGS_KEY = "bestcut/split_hash_distance"
# 나누기 기준 입력칸을 처음 켤 때의 시작값. 기능 자체는 기본으로 꺼져 있어 이 값이
# 몰래 적용되지는 않는다. 공개 샘플 측정(같은 사진 변형 0~12, 다른 사진 28~36)의 중간.
SPLIT_DISTANCE_START = 20
SPLIT_DISTANCE_MIN = 1
SPLIT_DISTANCE_MAX = HASH_BITS - 1
KEEP_PERCENT_SETTINGS_KEY = "bestcut/keep_percent"
XMP_RATING_SETTINGS_KEY = "bestcut/xmp_rating"
_XMP_TOOLTIP = (
    "<p>파일을 옮기지 않고, 라이트룸에서 바로 걸러 볼 수 있게 RAW 옆에 같은 이름의 "
    ".xmp를 씁니다: 베스트컷은 고른 별점, C컷은 거부 표시. RAW 파일 자체는 건드리지 "
    "않습니다.</p><p>라이트룸은 RAW만 옆의 .xmp를 읽어서 JPG만 있는 사진은 건너뜁니다. "
    "이미 .xmp가 있으면(보정 기록 등) 다른 내용은 그대로 두고 별점·거부 칸만 바꾸고, "
    "바꾸기 전 파일은 백업해 둡니다.</p><p>라이트룸에 이미 가져온 사진은 사진을 고르고 "
    "<b>메타데이터 → 파일에서 메타데이터 읽기</b>를 해야 반영됩니다. 거부 표시는 "
    "Lightroom Classic 13.2 이상에서 읽힙니다.</p>"
)
SETTINGS_COLLAPSED_KEY = "bestcut/settings_collapsed"
BADCUT_DIR_SETTINGS_KEY = "badcut/output_dir"  # 예전 C컷 화면과 같은 키(마지막 폴더 이어 쓰기)


NO_BADCUT = BadCutThresholds(min_sharpness=0.0, min_eyes_open=0.0)


class _SelectionSettings(NamedTuple):
    """미리보기 결과를 만든 기준. 바뀌면 캐시된 점수로 다시 묶고 다시 고른다."""

    gap_seconds: float
    split_distance: int | None  # 비슷하지 않은 사진 나누기 기준, 꺼져 있으면 None
    keep_percent: int  # 장면마다 남길 비율(%), 0이면 1장
    thresholds: BadCutThresholds = NO_BADCUT  # C컷 기준, 둘 다 0이면 C컷 판정 안 함
    clock_offsets: tuple[tuple[str, float], ...] = ()  # 바디별 시차(clock_sync), 장면 묶기에 씀
    scene_edits: tuple[tuple[Path, str], ...] = ()  # 직접 나누기/합치기(grouping.apply_scene_edits)


def _scene_key(settings: _SelectionSettings) -> tuple:
    """장면 묶음을 바꾸는 기준(간격·나누기·바디 시차). 남길 비율·C컷 기준은 장면을 안 바꾼다."""
    return settings.gap_seconds, settings.split_distance, settings.clock_offsets, settings.scene_edits


def _exclude_for(thresholds: BadCutThresholds):
    """C컷을 베스트컷 후보에서 빼는 함수(build_report의 exclude). 기준이 없으면 None."""
    if thresholds == NO_BADCUT:
        return None
    return lambda score: badcut_reason(score, thresholds)


def _is_badcut(row: ReportRow) -> bool:
    """C컷으로 옮길 사진: C컷 판정이고, 사용자가 직접 넣지 않은 것."""
    return row.badcut_reason is not None and not row.selected

# 스핀박스 화살표를 누르고 있으면 값이 연달아 바뀌므로, 마지막 변경 후 이만큼 조용하면
# 그때 한 번만 다시 묶는다.
REGROUP_DELAY_MS = 300


def _compare_state(row: ReportRow) -> tuple[str, str]:
    """나란히 비교 창의 칸 제목 오른쪽 글자와 색."""
    manual = " (직접)" if row.manual_choice is not None else ""
    if row.selected:
        return "✓ 남김" + manual, STATE_KEPT
    if _is_badcut(row):
        return f"C컷: {row.badcut_reason}", STATE_FLAGGED
    return "빠짐" + manual, STATE_NONE


def _describe_choice(row: ReportRow) -> str:
    """크게 보기 창 위쪽에 보여줄 이 컷의 상태."""
    state = "선택" if row.selected else (f"C컷({row.badcut_reason})" if _is_badcut(row) else "제외")
    if row.manual_choice is not None:
        state += "(직접)"
    return f"장면 {row.scene_id} · {state} · 종합점수 {row.total:.2f}"


_DIFF_TOOLTIP = (
    "<p><b>앞 사진과 차이(0~64)</b>: 촬영 시각순으로 바로 앞 사진과 얼마나 다르게 "
    "생겼는지입니다. 사진을 아주 작게 줄여 만든 지문(pHash)을 비교한 값이라 색·밝기·"
    "약간의 흔들림에는 둔하고, 구도·피사체가 바뀌면 커집니다.</p>"
    "<p>공개 샘플로 재 보면 같은 사진을 조금 옮기거나 밝게 한 것은 0~12, 서로 다른 "
    "사진은 28~36 정도였습니다. ✂ 표시는 나누기를 켰을 때 이 값이 기준을 넘어 장면이 "
    "나뉘는 자리입니다.</p>"
)


def _make_groups(
    photos: list[PhotoMetadata],
    hashes: dict[Path, int | None],
    gap_seconds: float,
    split_distance: int | None,
    clock_offsets: tuple[tuple[str, float], ...] = (),
    scene_edits: tuple[tuple[Path, str], ...] = (),
) -> list[list[PhotoMetadata]]:
    """장면으로 묶는다. 바디별 시차가 있으면 맞춘 시각으로(원래 사진 목록은 그대로), 마지막에
    사용자가 직접 나누거나 합친 경계를 덮어쓴다."""
    photos = shift_photos(photos, dict(clock_offsets))
    groups = group_by_time_gap(photos, gap_seconds=gap_seconds)
    if split_distance is not None:
        groups = split_dissimilar(groups, hashes, split_distance)
    return apply_scene_edits(groups, dict(scene_edits))


def _percent(done: int, total: int) -> int:
    return int(done / total * 100) if total else 0


class PreviewWorker(CancellableWorker):
    progress = Signal(int, int)
    status = Signal(str)
    # list[PhotoMetadata], list[ReportRow], _SelectionSettings, {경로: 지문}
    finished_ok = Signal(list, list, object, object)
    failed = Signal(str)

    def __init__(
        self,
        input_dirs: list[Path],
        settings: _SelectionSettings = _SelectionSettings(DEFAULT_SCENE_GAP_SECONDS, None, 0),
        recursive: bool = True,
        scan_cache: dict | None = None,
        score_cache: dict | None = None,
        thumbnail_cache: dict | None = None,
        hash_cache: dict | None = None,
    ) -> None:
        super().__init__()
        self._input_dirs = input_dirs
        self._selection_settings = settings
        self._hash_cache = hash_cache
        self._recursive = recursive
        self._scan_cache = scan_cache
        self._score_cache = score_cache
        self._thumbnail_cache = thumbnail_cache

    def run(self) -> None:
        try:
            def on_scan_progress(done: int, total: int, path: Path) -> None:
                self.check_cancel()
                self.progress.emit(done, total)
                self.status.emit(f"스캔 중: {path.name} ({done}/{total}, {_percent(done, total)}%)")

            photos = scan_and_extract_many(
                self._input_dirs,
                recursive=self._recursive,
                on_progress=on_scan_progress,
                cache=self._scan_cache,
                include_unpaired_raw=True,  # RAW만 찍은 폴더도 (미리보기 JPEG로)
            )

            def on_hash_progress(done: int, total: int) -> None:
                self.check_cancel()
                self.progress.emit(done, total)
                self.status.emit(f"사진 유사도 계산 중... ({done}/{total}, {_percent(done, total)}%)")

            # 나누기를 꺼둔 상태여도 계산해둔다 — 나중에 켜면 즉시 다시 묶을 수 있게.
            hashes = hashes_for(
                [p.path for p in photos], cache=self._hash_cache, on_progress=on_hash_progress
            )

            def on_score_progress(done: int, total: int) -> None:
                self.check_cancel()
                self.progress.emit(done, total)
                self.status.emit(f"사진 채점 중... ({done}/{total}, {_percent(done, total)}%)")

            # 먼저 사진 전체를 여러 장 동시에 채점해 둔다(장면별로 고를 때는 저장된 점수를 씀).
            score_photos(photos, on_progress=on_score_progress, cache=self._score_cache)
            settings = self._selection_settings
            groups = _make_groups(
                photos, hashes, settings.gap_seconds, settings.split_distance, settings.clock_offsets,
                settings.scene_edits,
            )

            def on_group_progress(done: int, total: int) -> None:
                self.check_cancel()
                self.progress.emit(done, total)
                self.status.emit(f"장면 그룹핑/스코어링 중... ({done}/{total}, {_percent(done, total)}%)")

            rows = build_report(
                groups,
                on_progress=on_group_progress,
                cache=self._score_cache,
                keep_percent=settings.keep_percent,
                exclude=_exclude_for(settings.thresholds),
            )
            self._generate_thumbnails(rows)
            self.finished_ok.emit(photos, rows, settings, hashes)
        except PreviewCancelled:
            self.cancelled.emit()
        except Exception as e:  # noqa: BLE001 - surfaced to the user, not swallowed
            log.exception("%s 실패", type(self).__name__)
            self.failed.emit(str(e))

    def _generate_thumbnails(self, rows: list[ReportRow]) -> None:
        if self._thumbnail_cache is None:
            return
        todo: list[tuple[Path, tuple[float, int]]] = []
        for row in rows:
            try:
                stat = row.path.stat()
            except OSError:
                continue
            key = (stat.st_mtime, stat.st_size)
            cached = self._thumbnail_cache.get(row.path)
            if cached is None or cached[:2] != key:
                todo.append((row.path, key))

        def on_progress(done: int, total: int) -> None:
            self.check_cancel()
            self.progress.emit(done, total)
            self.status.emit(f"썸네일 만드는 중... ({done}/{total}, {_percent(done, total)}%)")

        # 갤러리용 큰 썸네일을 JPEG로 압축해 보관(표는 이것을 줄여 쓴다). 여러 장 동시에
        thumbnails = map_in_order(
            lambda item: make_thumbnail_jpeg(item[0], THUMBNAIL_SIZE), todo, on_progress=on_progress
        )
        for (path, key), data in zip(todo, thumbnails):
            self._thumbnail_cache[path] = (*key, data)


def _is_unscored(row: ReportRow) -> bool:
    return "점수 계산 실패" in row.reason


class _SortItem(QTableWidgetItem):
    """보이는 글자와 따로 정렬 기준(sort_key)을 갖는 칸. 값이 없으면(None) 맨 뒤로."""

    def __init__(self, text: str, sort_key=None) -> None:
        super().__init__(text)
        self.sort_key = sort_key

    def __lt__(self, other: QTableWidgetItem) -> bool:
        if not isinstance(other, _SortItem):
            return super().__lt__(other)
        if self.sort_key is None or other.sort_key is None:
            return self.sort_key is not None and other.sort_key is None
        return self.sort_key < other.sort_key


def _bar_item(text: str, fraction: float | None, sort_key=None) -> QTableWidgetItem:
    """글자 + 칸 아래 막대(0~1, None이면 막대 없음). 정렬은 sort_key(없으면 fraction)로."""
    item = _SortItem(text, sort_key if sort_key is not None else fraction)
    if fraction is not None:
        item.setData(BAR_ROLE, fraction)
    return item


def _sharpness_ranks(rows: list[ReportRow]) -> list[tuple[int, int, float]]:
    """줄마다 (장면 안 선명도 순위, 장면 사진 수, 장면 안 0~1 위치). 선명도 원래 값은
    해상도·피사체마다 범위가 달라 장면끼리 비교할 수 없어, 같은 장면 안에서만 줄 세운다
    (0~1은 종합점수 계산과 같은 방식 — 장면에서 가장 흐린 컷 0, 가장 선명한 컷 1)."""
    scenes: dict[int, list[int]] = {}
    for i, row in enumerate(rows):
        if not _is_unscored(row):
            scenes.setdefault(row.scene_id, []).append(i)
    result = [(0, 0, 0.0)] * len(rows)
    for members in scenes.values():
        values = [rows[i].sharpness for i in members]
        lo, hi = min(values), max(values)
        ordered = sorted(members, key=lambda i: rows[i].sharpness, reverse=True)
        for rank, i in enumerate(ordered, start=1):
            fraction = 1.0 if hi == lo else (rows[i].sharpness - lo) / (hi - lo)
            result[i] = (rank, len(members), fraction)
    return result


THUMBNAIL_SIZE = (GALLERY_THUMB, GALLERY_THUMB)
TABLE_ICON = 64
# 파일명 칸에 넣어 두는 결과 번호(self._rows 안 위치) — 표를 정렬해도 사진을 찾을 수 있게
_INDEX_ROLE = Qt.ItemDataRole.UserRole + 1
VIEW_SETTINGS_KEY = "bestcut/view"  # "gallery" | "table"


class ApplyWorker(QThread):
    progress = Signal(int, int)
    finished_ok = Signal(list, object)  # list[FileOpResult], 판정 리포트 경로
    # 오류 메시지와, 실패 전에 이미 끝낸 파일 작업(이동이었다면 되돌릴 수 있게)
    failed = Signal(str, list)

    def __init__(
        self, rows: list[ReportRow], output_dir: Path, move: bool, kind: str = "bestcut"
    ) -> None:
        super().__init__()
        self._rows = rows
        self._output_dir = output_dir
        self._move = move
        self._kind = kind  # 판정 리포트 파일 이름 앞부분(bestcut/badcut)

    def run(self) -> None:
        try:
            results = apply_selection(
                self._rows,
                self._output_dir,
                move=self._move,
                on_progress=self.progress.emit,
            )
            # 파일 작업이 끝난 뒤에 쓴다 — 출력 폴더가 원본 폴더와 겹쳐 apply_selection이
            # 막은 경우엔 원본 폴더에 리포트가 생기지 않도록.
            report_path = write_apply_report(self._rows, self._output_dir, self._kind)
            self.finished_ok.emit(results, report_path)
        except ApplyError as e:
            self.failed.emit(str(e), e.results)
        except Exception as e:  # noqa: BLE001 - surfaced to the user, not swallowed
            log.exception("%s 실패", type(self).__name__)
            self.failed.emit(str(e), [])


class XmpWorker(QThread):
    """라이트룸 별점을 RAW 옆 .xmp에 쓴다(수천 장이면 잠깐 걸려 스레드로)."""

    finished_ok = Signal(list)  # list[XmpResult]
    failed = Signal(str)

    def __init__(self, rows: list[XmpPlanRow], rating: int, run_dir: Path) -> None:
        super().__init__()
        self._args = (rows, rating, run_dir)

    def run(self) -> None:
        try:
            self.finished_ok.emit(write_xmp(*self._args))
        except Exception as e:  # noqa: BLE001 - surfaced to the user, not swallowed
            log.exception("%s 실패", type(self).__name__)
            self.failed.emit(str(e))


class UndoWorker(QThread):
    """방금 옮긴 사진을 원래 폴더로 되돌린다(다른 드라이브면 복사라 오래 걸릴 수 있어 스레드로)."""

    finished_ok = Signal(object)  # UndoOutcome
    failed = Signal(str)

    def __init__(self, results: list[FileOpResult], log_dir: Path) -> None:
        super().__init__()
        self._results = results
        self._log_dir = log_dir

    def run(self) -> None:
        try:
            self.finished_ok.emit(undo_moves(self._results, self._log_dir))
        except Exception as e:  # noqa: BLE001 - surfaced to the user, not swallowed
            log.exception("%s 실패", type(self).__name__)
            self.failed.emit(str(e))


@dataclass
class _LastMove:
    """되돌릴 수 있는 마지막 이동(앱을 끄면 잊는다 — 폴더 정리의 실행 취소와 같게)."""

    kind: str  # "badcut" | "bestcut"
    results: list[FileOpResult]
    log_dir: Path
    # 옮기기 직전·직후의 미리보기 사진 목록과 직접 고른 것. 그 뒤로 새로 미리보기하지
    # 않았으면 되돌린 사진을 다시 넣어 바로 다시 묶는다.
    photos_before: list[PhotoMetadata] | None
    photos_after: list[PhotoMetadata] | None
    choices_before: dict[Path, bool]


class BestCutTab(QWidget):
    def __init__(self, inputs: InputFolderPanel | None = None) -> None:
        super().__init__()

        # 입력 폴더는 사진 선별 탭들이 같이 쓰는 칸(없으면 이 탭 혼자 쓰는 칸을 만든다)
        self._owns_inputs = inputs is None
        self._inputs = inputs if inputs is not None else InputFolderPanel()
        self._inputs.changed.connect(self._invalidate_preview)
        self._output_dir: Path | None = None
        self._badcut_dir: Path | None = None  # C컷을 옮길 폴더
        # 실제 판정에 쓰는 C컷 기준. 스핀박스는 반올림해 보여주기만 하므로(기준 사진 눈뜸
        # 0.456이 0.46으로 반올림되면 기준 사진 자신이 C컷이 됨), 기준 사진을 고르면 그
        # 사진의 정확한 점수를 여기 둔다. 0이면 그 판정은 끔(기본값 없음 — CLAUDE.md).
        self._sharpness_threshold = 0.0
        self._eyes_threshold = 0.0
        self._sharpness_ref_name: str | None = None
        self._eyes_ref_name: str | None = None
        self._rows: list[ReportRow] | None = None
        # 자동 선정 결과(직접 고른 것 반영 전)와, 사용자가 직접 고른 것(경로 → 선택 여부).
        # 화면·적용에 쓰는 self._rows는 둘을 합친 결과다.
        self._auto_rows: list[ReportRow] | None = None
        self._choices: dict[Path, bool] = {}
        # 직접 나누거나 합친 장면 경계(사진 경로 → "split"/"join"). 앱을 꺼도 남는다(choice_store)
        self._scene_edits: dict[Path, str] = {}
        self._viewer: PhotoViewerDialog | None = None
        self._compare: CompareDialog | None = None
        # 나란히 비교 창 아래 줄에 쓰는 경로 → (장면 안 선명도 순위, 장면 사진 수)
        self._ranks_by_path: dict[Path, tuple[int, int]] = {}
        # 표에 지금 채워진 결과(같은 사진·순서면 바뀐 줄만 다시 채우려고)와 그때의 나누기 기준
        self._table_rows: list[ReportRow] | None = None
        self._table_split: int | None = None
        self._viewer_paths: list[Path] = []
        # 마지막 미리보기에서 스캔한 사진. 간격만 바꿔 다시 묶을 때 재사용한다.
        self._photos: list[PhotoMetadata] | None = None
        # 지금 화면의 결과를 만든 기준
        self._rows_grouping: _SelectionSettings | None = None
        self._hashes: dict[Path, int | None] = {}
        self._distances: dict[Path, int | None] = {}
        self._preview_worker: PreviewWorker | None = None
        self._apply_worker: ApplyWorker | None = None
        self._badcut_worker: ApplyWorker | None = None
        self._undo_worker: UndoWorker | None = None
        self._xmp_worker: XmpWorker | None = None
        self._last_move: _LastMove | None = None
        # 되돌릴 수 있는 마지막 라이트룸 별점 쓰기(결과, 백업·기록 폴더). 이동과 둘 중 최근 것 하나만
        self._last_xmp: tuple[list[XmpResult], Path] | None = None
        self._busy = False
        # 같은 세션(앱 종료 전까지) 동안 이미 스캔/스코어링한 파일은 다시 처리하지
        # 않도록 재사용하는 인메모리 캐시. (mtime, size)가 바뀐 파일만 다시 계산됨.
        self._scan_cache: dict = {}
        # 점수·지문은 앱을 꺼도 남는 공용 저장소를 쓴다(C컷 탭과 공유, score_store).
        self._score_cache: dict = score_store.score_cache()
        self._thumbnail_cache: dict = {}
        # (경로, 크기) → (썸네일 키, QPixmap). 표·갤러리를 다시 그릴 때 JPEG를 다시 풀지 않게
        self._pixmap_cache: dict = {}
        self._hash_cache: dict = score_store.hash_cache()
        self.setAcceptDrops(True)
        self._settings = make_settings()

        self._output_label = PathLabel()
        output_button = compact(QPushButton("선택…"))
        output_button.clicked.connect(self._choose_output_dir)
        self._badcut_label = PathLabel()
        badcut_button = compact(QPushButton("선택…"))
        badcut_button.clicked.connect(self._choose_badcut_dir)

        # C컷 기준(기본 0 = 끔)
        self._min_sharpness_spin = QDoubleSpinBox()
        self._min_sharpness_spin.setRange(0.0, 1_000_000.0)
        self._min_sharpness_spin.setDecimals(1)
        self._min_sharpness_spin.setSingleStep(10.0)
        self._min_sharpness_spin.setToolTip(_BADCUT_SHARPNESS_TOOLTIP + _MANUAL_SPIN_TOOLTIP)
        self._min_sharpness_spin.valueChanged.connect(self._on_sharpness_spin_changed)
        self._min_eyes_open_spin = QDoubleSpinBox()
        self._min_eyes_open_spin.setRange(0.0, 1.0)
        self._min_eyes_open_spin.setDecimals(2)
        self._min_eyes_open_spin.setSingleStep(0.05)
        self._min_eyes_open_spin.setToolTip(_BADCUT_EYES_TOOLTIP + _MANUAL_SPIN_TOOLTIP)
        self._min_eyes_open_spin.valueChanged.connect(self._on_eyes_spin_changed)
        # 기준 사진 이름이 길어도 칸이 넓어지지 않게(가운데 …, 전체는 툴팁)
        self._sharpness_ref_label = PathLabel("꺼짐")
        self._eyes_ref_label = PathLabel("꺼짐")
        self._sharpness_ref_button = compact(QPushButton("선명도 기준"))
        self._sharpness_ref_button.setToolTip(
            "<p>표·갤러리에서 고른 사진의 선명도까지는 남기고, 이보다 흐린 사진을 "
            "C컷(블러)으로 봅니다.</p>"
        )
        self._sharpness_ref_button.clicked.connect(self._use_selected_as_sharpness_reference)
        self._eyes_ref_button = compact(QPushButton("눈뜸 기준"))
        self._eyes_ref_button.setToolTip(
            "<p>고른 사진의 눈 뜬 정도까지는 남기고, 이보다 눈이 더 감긴 사진을 "
            "C컷(눈감음)으로 봅니다. 얼굴이 검출된 사진만 고를 수 있습니다.</p>"
        )
        self._eyes_ref_button.clicked.connect(self._use_selected_as_eyes_reference)
        self._update_reference_labels()

        self._copy_radio = QRadioButton("복사 (원본 유지)")
        self._copy_radio.setChecked(True)
        self._move_radio = QRadioButton("이동")
        self._move_radio.toggled.connect(lambda _on: self._update_apply_buttons())

        self._undo_move_button = compact(QPushButton("↶ 방금 이동 되돌리기"))
        self._undo_move_button.setToolTip(
            "<p>방금 옮긴 C컷(또는 베스트컷 이동)을 원래 폴더로 되돌립니다(같이 옮긴 RAW 포함). "
            "원래 자리에 같은 이름의 파일이 새로 생겼으면 그 사진은 덮어쓰지 않고 둡니다.</p>"
            "<p>마지막 이동 한 번만, 앱을 끄기 전까지 되돌릴 수 있습니다.</p>"
        )
        self._undo_move_button.setVisible(False)
        self._undo_move_button.clicked.connect(self._undo_last_move)

        # 바디별 시계 맞추기(시간순 정렬 화면과 같이 씀). 바꾸면 저장된 점수로 즉시 다시 묶는다
        self._clock_button = compact(QPushButton("맞추기…"))
        self._clock_button.setToolTip(
            "<p>바디(카메라)마다 시계가 어긋났으면 시차를 넣어 맞춘 시각으로 장면을 묶습니다. "
            "같은 순간을 찍은 두 사진으로 계산할 수도 있습니다. 원본 파일의 촬영 시각은 바꾸지 "
            "않습니다.</p>"
        )
        self._clock_button.clicked.connect(self._open_clock_sync)
        self._clock_status = QLabel()
        offset_store().changed.connect(self._on_clock_changed)
        self._update_clock_status()

        # 라이트룸 별점(RAW 옆 .xmp): 베스트컷 별점 + C컷 거부
        self._xmp_rating_spin = QSpinBox()
        self._xmp_rating_spin.setRange(RATING_MIN, RATING_MAX)
        self._xmp_rating_spin.setSuffix(" ★")
        self._xmp_rating_spin.setValue(
            int(load_float(self._settings, XMP_RATING_SETTINGS_KEY, 3, RATING_MIN, RATING_MAX))
        )
        self._xmp_rating_spin.valueChanged.connect(
            lambda value: save_float(self._settings, XMP_RATING_SETTINGS_KEY, value)
        )
        self._xmp_button = compact(QPushButton("별점 쓰기"))
        self._xmp_button.setToolTip(_XMP_TOOLTIP)
        self._xmp_button.clicked.connect(self._run_xmp)

        self._cancel_preview_button = compact(QPushButton("취소"))
        self._cancel_preview_button.setEnabled(False)
        self._cancel_preview_button.setToolTip(
            "<p>진행 중인 미리보기(스캔·채점)를 멈춥니다. 그때까지 계산한 점수는 저장돼, "
            "다시 미리보기하면 이어서 계산합니다.</p>"
        )
        self._cancel_preview_button.clicked.connect(self._cancel_preview)
        self._preview_button = make_primary(QPushButton("미리보기"))
        self._preview_button.setToolTip(
            "<p>사진을 장면별로 묶고 장면마다 베스트컷을 고르고, C컷 기준을 정했으면 C컷도 "
            "판정한 결과를 보여줍니다. 파일은 아직 복사/이동하지 않습니다.</p>"
        )
        self._preview_button.clicked.connect(self._run_preview)

        self._gap_spin = QDoubleSpinBox()
        self._gap_spin.setRange(SCENE_GAP_MIN_SECONDS, SCENE_GAP_MAX_SECONDS)
        self._gap_spin.setDecimals(1)
        self._gap_spin.setSingleStep(0.5)
        self._gap_spin.setSuffix(" 초")
        self._gap_spin.setValue(
            load_float(
                self._settings,
                SCENE_GAP_SETTINGS_KEY,
                DEFAULT_SCENE_GAP_SECONDS,
                SCENE_GAP_MIN_SECONDS,
                SCENE_GAP_MAX_SECONDS,
            )
        )
        self._regroup_timer = QTimer(self)
        self._regroup_timer.setSingleShot(True)
        self._regroup_timer.setInterval(REGROUP_DELAY_MS)
        self._regroup_timer.timeout.connect(self._regroup)
        self._gap_spin.valueChanged.connect(self._on_gap_changed)
        gap_label = QLabel("장면 간격 ⓘ:")
        self._gap_widgets = (gap_label, self._gap_spin)

        self._split_checkbox = QCheckBox("비슷하지 않은 사진은 장면 나누기")
        self._split_checkbox.setChecked(load_bool(self._settings, SPLIT_ENABLED_SETTINGS_KEY, False))
        self._split_spin = QSpinBox()
        self._split_spin.setRange(SPLIT_DISTANCE_MIN, SPLIT_DISTANCE_MAX)
        self._split_spin.setPrefix("앞 사진과 차이가 ")
        self._split_spin.setSuffix(" 넘으면")
        self._split_spin.setValue(
            int(
                load_float(
                    self._settings,
                    SPLIT_DISTANCE_SETTINGS_KEY,
                    SPLIT_DISTANCE_START,
                    SPLIT_DISTANCE_MIN,
                    SPLIT_DISTANCE_MAX,
                )
            )
        )
        self._split_spin.setEnabled(self._split_checkbox.isChecked())
        self._split_checkbox.toggled.connect(self._on_split_changed)
        self._split_spin.valueChanged.connect(self._on_split_changed)
        # 체크박스와 기준 입력칸을 두 줄로 — 한 줄이면 칸이 너무 넓어진다
        split_row = QVBoxLayout()
        split_row.addWidget(self._split_checkbox)
        split_spin_row = QHBoxLayout()
        split_spin_row.addSpacing(22)
        split_spin_row.addWidget(self._split_spin)
        split_spin_row.addStretch()
        split_row.addLayout(split_spin_row)

        self._keep_spin = QSpinBox()
        self._keep_spin.setRange(0, 100)
        self._keep_spin.setSingleStep(5)
        self._keep_spin.setSuffix(" %")
        self._keep_spin.setValue(
            int(load_float(self._settings, KEEP_PERCENT_SETTINGS_KEY, 0, 0, 100))
        )
        self._keep_spin.valueChanged.connect(self._on_keep_changed)
        keep_label = QLabel("남길 비율 ⓘ:")
        self._keep_hint = QLabel()
        self._keep_widgets = (keep_label, self._keep_spin, self._keep_hint)
        keep_row = QHBoxLayout()
        keep_row.addWidget(self._keep_spin)
        keep_row.addWidget(self._keep_hint)
        keep_row.addStretch()

        self._grouping_help_label = QLabel("ⓘ 장면은 어떻게 묶나요?")
        self._scoring_help_label = QLabel("ⓘ 베스트컷은 어떻게 고르나요?")
        help_row = QHBoxLayout()
        help_row.addWidget(self._gap_spin)
        help_row.addStretch()

        self._apply_button = make_primary(QPushButton("베스트컷 복사"))
        self._apply_button.setEnabled(False)
        self._apply_button.clicked.connect(self._run_apply)
        self._badcut_apply_button = make_danger(QPushButton("C컷 이동"))
        self._badcut_apply_button.setToolTip(
            "<p>C컷으로 판정된 사진(직접 넣은 컷은 빼고)을 C컷 폴더로 <b>옮깁니다</b>. 원본 "
            "폴더에서는 빠지고, 같은 이름의 RAW도 함께 옮깁니다.</p>"
        )
        self._badcut_apply_button.setEnabled(False)
        self._badcut_apply_button.clicked.connect(self._run_badcut_apply)

        self._open_output_button = compact(QPushButton("열기"))
        self._open_output_button.setToolTip("<p>베스트컷을 복사한 폴더를 엽니다.</p>")
        self._open_output_button.setEnabled(False)
        self._open_output_button.clicked.connect(self._open_output_folder)

        self._progress_bar = EtaProgressBar()
        self._status_label = QLabel("")
        self._summary_text = SummaryLabel()
        self._next_steps = NextStepBar()

        self._table = QTableWidget(0, 10)
        self._table.setHorizontalHeaderLabels(
            [
                "썸네일",
                "파일명",
                "장면",
                "앞 사진과 차이",
                "선택여부",
                "종합점수",
                "선명도",
                "노출",
                "눈뜸",
                "사유",
            ]
        )
        for column, tooltip in (
            (3, _DIFF_TOOLTIP),
            (5, _TOTAL_TOOLTIP),
            (6, _SHARPNESS_TOOLTIP),
            (7, _EXPOSURE_TOOLTIP),
            (8, _EYES_TOOLTIP),
        ):
            self._table.horizontalHeaderItem(column).setToolTip(tooltip)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setIconSize(QSize(TABLE_ICON, TABLE_ICON))
        self._table.setItemDelegate(BarDelegate(self._table))
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        # 머리글을 누르면 정렬(C컷 기준 사진을 고를 때 선명도·눈뜸 순으로). '장면' 머리글은
        # 원래 촬영 순서로 되돌린다. 정렬해도 줄 → 결과를 찾도록 파일명 칸에 결과 번호를 둔다.
        self._table.setSortingEnabled(True)
        self._table.horizontalHeader().sortIndicatorChanged.connect(lambda *_: self._apply_row_filter())
        self._table.itemSelectionChanged.connect(self._update_choice_buttons)
        self._table.itemSelectionChanged.connect(
            lambda: self._gallery.set_current(self._selected_table_row())
        )
        self._table.cellDoubleClicked.connect(lambda table_row, _c: self._open_viewer(self._index_at(table_row)))
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_table_context_menu)

        self._view_button = compact(QPushButton("크게 보기"))
        self._view_button.setToolTip(
            "<p>선택한 사진을 크게 엽니다. 같은 장면의 컷끼리 ←/→로 넘겨보며 고를 수 "
            "있습니다. 표에서 사진을 더블클릭해도 됩니다.</p>"
        )
        self._view_button.clicked.connect(self._open_viewer_for_selected)
        self._compare_button = compact(QPushButton("나란히 비교"))
        self._compare_button.setToolTip(
            "<p>고른 사진과 같은 장면의 컷을 2~4장 나란히 놓고 비교합니다. 확대·이동이 모든 "
            "칸에 함께 적용돼, 같은 자리(눈·초점)를 같은 배율로 볼 수 있습니다. (C)</p>"
        )
        self._compare_button.clicked.connect(self._open_compare_for_selected)
        # 장면 직접 고치기: 시간 간격으로 잘못 갈리거나 붙은 장면을 손으로 바로잡는다
        self._split_action = QAction("여기서 나누기 (S)", self)
        self._split_action.setToolTip("고른 사진부터 새 장면으로 나눕니다.")
        self._split_action.triggered.connect(lambda: self._edit_scene_at_selected(SPLIT))
        self._join_action = QAction("앞 장면과 합치기 (M)", self)
        self._join_action.setToolTip("고른 사진이 속한 장면을 바로 앞 장면에 붙입니다.")
        self._join_action.triggered.connect(lambda: self._edit_scene_at_selected(JOIN))
        self._reset_scenes_action = QAction("직접 고친 장면 모두 되돌리기", self)
        self._reset_scenes_action.triggered.connect(self._reset_scene_edits)
        scene_menu = QMenu(self)
        scene_menu.addAction(self._split_action)
        scene_menu.addAction(self._join_action)
        scene_menu.addSeparator()
        scene_menu.addAction(self._reset_scenes_action)
        self._scene_button = QToolButton()
        self._scene_button.setText("장면")
        self._scene_button.setToolTip(
            "<p>장면을 직접 고칩니다: 고른 사진부터 <b>나누기(S)</b>, 고른 사진의 장면을 "
            "<b>앞 장면과 합치기(M)</b>. 간격·나누기 기준을 바꿔도, 앱을 꺼도 유지됩니다.</p>"
        )
        self._scene_button.setMenu(scene_menu)
        self._scene_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._toggle_button = compact(QPushButton("선택에 넣기"))
        self._toggle_button.setToolTip(
            "<p>표에서 고른 사진을 선택에 넣거나 뺍니다. 장면마다 자동으로 1장을 고르지만, "
            "원하는 컷을 더 넣거나 자동으로 고른 컷을 뺄 수 있습니다.</p>"
        )
        self._toggle_button.clicked.connect(self._toggle_selected_row)
        self._reset_choices_button = compact(QPushButton("자동 선택으로 되돌리기"))
        self._reset_choices_button.setToolTip("<p>직접 넣거나 뺀 것을 모두 취소합니다.</p>")
        self._reset_choices_button.clicked.connect(self._reset_choices)
        # 결과 보기: 장면별 갤러리(큰 썸네일) / 표(숫자 비교). 고른 사진은 둘이 함께 쓴다
        self._gallery = SceneGallery(self._thumbnail_pixmap)
        self._gallery.photo_clicked.connect(self._select_index)
        self._gallery.photo_activated.connect(self._open_viewer)
        self._gallery.toggle_requested.connect(self._toggle_row)
        self._gallery.choice_requested.connect(self._choose_row)
        self._gallery.compare_requested.connect(self._open_compare)
        self._gallery.scene_edit_requested.connect(self._edit_scene)
        self._gallery.context_requested.connect(self._show_context_menu)
        # 표에서도 크게 보기 창과 같은 단축키로 고른다
        for key, action in (
            ("Space", self._toggle_selected_row),
            ("P", lambda: self._choose_selected_row(True)),
            ("X", lambda: self._choose_selected_row(False)),
            ("Return", self._open_viewer_for_selected),
            ("Enter", self._open_viewer_for_selected),
            ("C", self._open_compare_for_selected),
            ("S", lambda: self._edit_scene_at_selected(SPLIT)),
            ("M", lambda: self._edit_scene_at_selected(JOIN)),
        ):
            QShortcut(QKeySequence(key), self._table, activated=action, context=Qt.ShortcutContext.WidgetShortcut)
        # 결과가 아직 없을 때 결과 자리에 띄우는 순서 안내
        self._guide = GuidePanel(
            "사진 고르는 순서",
            [
                "위 입력 폴더 칸에 사진 폴더 넣기 (끌어다 놓아도 됩니다)",
                "베스트컷을 복사할 폴더 고르기",
                "미리보기 — 장면별로 묶고 장면마다 베스트컷을 골라 보여줍니다 (파일은 아직 그대로)",
                "(선택) C컷 기준 정하기 — 흐리거나 눈 감은 사진을 C컷으로 걸러 베스트컷 후보에서 뺍니다",
                "직접 넣기/빼기 (Space · P · X) → 베스트컷 복사, 필요하면 C컷 이동",
            ],
        )
        self._results = QStackedWidget()
        self._results.addWidget(self._guide)
        self._results.addWidget(self._gallery)
        self._results.addWidget(self._table)
        self._only_kept_checkbox = QCheckBox("남기는 컷만 보기")
        self._only_kept_checkbox.setToolTip("<p>장면마다 남기는(선택된) 컷만 보여줍니다. 빠진 컷을 다시 넣으려면 끄세요.</p>")
        self._only_kept_checkbox.toggled.connect(self._on_only_kept_toggled)
        self._gallery_button = QToolButton()
        self._gallery_button.setText("갤러리")
        self._gallery_button.setToolTip(
            "<p>장면마다 큰 썸네일로 봅니다. 클릭해 고르고, 더블클릭하면 크게 보기, "
            "Space 넣기/빼기, P 넣기, X 빼기, ←/→ 앞뒤 사진, ↑/↓ 앞뒤 장면.</p>"
        )
        self._table_button = QToolButton()
        self._table_button.setText("표")
        self._table_button.setToolTip("<p>점수를 숫자로 비교하는 표로 봅니다.</p>")
        view_group = QButtonGroup(self)
        for button in (self._gallery_button, self._table_button):
            button.setCheckable(True)
            view_group.addButton(button)
        self._gallery_button.toggled.connect(self._on_view_toggled)
        if self._settings.value(VIEW_SETTINGS_KEY, "gallery") == "table":
            self._table_button.setChecked(True)
        else:
            self._gallery_button.setChecked(True)

        self._shortcut_hint = QLabel("Space·P·X 고르기 · Enter 크게 · C 비교")
        self._shortcut_hint.setToolTip(
            "<p>Space 넣기/빼기 · P 넣기 · X 빼기 · Enter(더블클릭) 크게 보기 · C 나란히 비교 · "
            "S 여기서 나누기 · M 앞 장면과 합치기 · 오른쪽 클릭 메뉴</p>"
            "<p>전체 단축키는 ? 키(또는 F1)나 도움말 › 단축키에서 볼 수 있습니다.</p>"
        )
        choice_button_row = QHBoxLayout()
        choice_button_row.addWidget(self._gallery_button)
        choice_button_row.addWidget(self._table_button)
        choice_button_row.addSpacing(12)
        choice_button_row.addWidget(self._view_button)
        choice_button_row.addWidget(self._compare_button)
        choice_button_row.addWidget(self._scene_button)
        choice_button_row.addWidget(self._toggle_button)
        choice_button_row.addWidget(self._reset_choices_button)
        choice_button_row.addSpacing(12)
        choice_button_row.addWidget(self._only_kept_checkbox)
        choice_button_row.addStretch()
        choice_button_row.addWidget(self._shortcut_hint)

        # 위: 출력(왼쪽) · 기준(오른쪽)을 나란히 두어 결과 표에 세로 공간을 몰아준다
        output_row = QHBoxLayout()
        output_row.addWidget(self._output_label, stretch=1)
        output_row.addWidget(output_button)
        output_row.addWidget(self._open_output_button)
        radio_row = QHBoxLayout()
        radio_row.addWidget(self._copy_radio)
        radio_row.addWidget(self._move_radio)
        radio_row.addStretch()
        badcut_row = QHBoxLayout()
        badcut_row.addWidget(self._badcut_label, stretch=1)
        badcut_row.addWidget(badcut_button)
        xmp_row = QHBoxLayout()
        xmp_row.addWidget(QLabel("베스트컷"))
        xmp_row.addWidget(self._xmp_rating_spin)
        xmp_row.addWidget(QLabel("· C컷 거부"))
        xmp_row.addStretch()
        xmp_row.addWidget(self._xmp_button)
        output_form = QFormLayout()
        output_form.addRow("베스트컷 폴더:", output_row)
        output_form.addRow("방식:", radio_row)
        output_form.addRow("C컷 폴더:", badcut_row)
        xmp_label = QLabel("라이트룸 ⓘ:")
        xmp_label.setToolTip(_XMP_TOOLTIP)
        output_form.addRow(xmp_label, xmp_row)
        output_group = QGroupBox("출력")
        output_group.setLayout(output_form)

        settings_form = QFormLayout()
        settings_form.addRow(gap_label, help_row)
        settings_form.addRow(keep_label, keep_row)
        clock_label = QLabel("바디 시계:")
        clock_label.setToolTip(self._clock_button.toolTip())
        clock_row = QHBoxLayout()
        clock_row.addWidget(self._clock_button)
        clock_row.addWidget(self._clock_status, stretch=1)
        settings_form.addRow(clock_label, clock_row)
        settings_form.addRow(split_row)
        settings_form.addRow(self._grouping_help_label)
        settings_form.addRow(self._scoring_help_label)
        settings_group = QGroupBox("장면·베스트컷 기준")
        settings_group.setLayout(settings_form)

        sharpness_label = QLabel("선명도 ⓘ:")
        sharpness_label.setToolTip(_BADCUT_SHARPNESS_TOOLTIP)
        eyes_label = QLabel("눈뜸 ⓘ:")
        eyes_label.setToolTip(_BADCUT_EYES_TOOLTIP)
        sharpness_row = QHBoxLayout()
        sharpness_row.addWidget(self._min_sharpness_spin)
        sharpness_row.addWidget(self._sharpness_ref_label, stretch=1)
        eyes_row = QHBoxLayout()
        eyes_row.addWidget(self._min_eyes_open_spin)
        eyes_row.addWidget(self._eyes_ref_label, stretch=1)
        reference_row = QHBoxLayout()
        reference_row.addWidget(QLabel("고른 사진을"))
        reference_row.addWidget(self._sharpness_ref_button)
        reference_row.addWidget(self._eyes_ref_button)
        reference_row.addStretch()
        badcut_form = QFormLayout()
        badcut_form.addRow(sharpness_label, sharpness_row)
        badcut_form.addRow(eyes_label, eyes_row)
        badcut_form.addRow(reference_row)
        badcut_group = QGroupBox("C컷 기준 (0 = 끔)")
        badcut_group.setLayout(badcut_form)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.addWidget(output_group, stretch=1)
        top_row.addWidget(settings_group, stretch=1)
        top_row.addWidget(badcut_group, stretch=1)
        # 결과를 볼 때는 설정 칸을 접어 갤러리·표에 세로 공간을 더 준다(마지막 상태 기억)
        self._settings_box = QWidget()
        self._settings_box.setLayout(top_row)
        self._collapse_button = QToolButton()
        self._collapse_button.setCheckable(True)
        self._collapse_button.setToolTip("<p>출력·기준 설정 칸을 접거나 폅니다.</p>")
        self._collapse_button.toggled.connect(self._on_collapse_toggled)

        # 실행 줄: 미리보기(왼쪽) → 적용(오른쪽) 순서로 읽히게, 주요 버튼만 강조
        button_row = QHBoxLayout()
        button_row.addWidget(self._collapse_button)
        button_row.addWidget(self._preview_button)
        button_row.addWidget(self._cancel_preview_button)
        button_row.addSpacing(12)
        button_row.addLayout(status_row(self._progress_bar, self._status_label), stretch=1)
        button_row.addWidget(self._undo_move_button)
        button_row.addWidget(self._badcut_apply_button)
        button_row.addWidget(self._apply_button)

        layout = QVBoxLayout()
        if self._owns_inputs:
            layout.addWidget(self._inputs)
        layout.addWidget(self._settings_box)
        layout.addLayout(button_row)
        layout.addWidget(self._summary_text)
        layout.addWidget(self._next_steps)
        layout.addLayout(choice_button_row)
        layout.addWidget(self._results, stretch=1)

        restored_output = load_folder(self._settings, "bestcut/output_dir")
        if restored_output is not None:
            self._output_dir = restored_output
            self._output_label.set_path(restored_output)
        restored_badcut = load_folder(self._settings, BADCUT_DIR_SETTINGS_KEY)
        if restored_badcut is not None:
            self._badcut_dir = restored_badcut
            self._badcut_label.set_path(restored_badcut)

        self._collapse_button.setChecked(load_bool(self._settings, SETTINGS_COLLAPSED_KEY, False))
        self._on_collapse_toggled(self._collapse_button.isChecked())
        self._update_grouping_help()
        self._update_choice_buttons()
        self._show_results()
        self.setLayout(layout)

    @property
    def is_busy(self) -> bool:
        return self._busy

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt override signature
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt override signature
        folders = extract_dropped_folders(event.mimeData())
        if folders:
            self._add_folders(folders)
        event.acceptProposedAction()

    @property
    def _input_dirs(self) -> list[Path]:
        return self._inputs.folders()

    @_input_dirs.setter
    def _input_dirs(self, folders: list[Path]) -> None:
        self._inputs.set_folders(folders)

    def _add_folders(self, folders: list[Path]) -> None:
        self._inputs.add_folders(folders)

    def _choose_output_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "출력 폴더 선택")
        if directory:
            self._output_dir = Path(directory)
            self._output_label.set_path(Path(directory))
            # 미리보기 결과(스캔/장면 그룹핑/스코어링)는 출력 폴더와 무관하므로
            # 다시 계산할 필요 없음 — 적용 버튼 활성화 여부만 갱신한다.
            self._apply_button.setEnabled(bool(self._rows))
            self._open_output_button.setEnabled(False)
            save_folder(self._settings, "bestcut/output_dir", self._output_dir)
            self._update_apply_buttons()
            self._update_guide()

    def _choose_badcut_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "C컷을 옮길 폴더 선택")
        if directory:
            self._badcut_dir = Path(directory)
            self._badcut_label.set_path(self._badcut_dir)
            save_folder(self._settings, BADCUT_DIR_SETTINGS_KEY, self._badcut_dir)
            self._update_apply_buttons()

    def _forget_move_snapshot(self) -> None:
        """미리보기가 바뀌면 되돌린 뒤 예전 사진 목록으로 다시 묶지 않게 한다(되돌리기는 그대로)."""
        if self._last_move is not None:
            self._last_move.photos_before = None

    def _invalidate_preview(self) -> None:
        self._next_steps.clear()
        self._forget_move_snapshot()
        self._rows = None
        self._auto_rows = None
        self._photos = None
        self._close_viewer()
        self._open_output_button.setEnabled(False)
        self._table.setRowCount(0)
        self._table_rows = None
        self._update_apply_buttons()
        self._gallery.set_scenes([])
        self._update_choice_buttons()
        self._show_results()

    def _run_preview(self) -> None:
        if self._busy:
            QMessageBox.warning(
                self, "작업 진행 중", "다른 작업이 진행 중입니다. 완료될 때까지 기다려주세요."
            )
            return

        if not self._input_dirs:
            QMessageBox.warning(self, "입력 폴더 없음", "먼저 입력 폴더를 하나 이상 추가하세요.")
            return

        self._busy = True
        self._preview_button.setEnabled(False)
        self._progress_bar.setValue(0)
        self._status_label.setText("스캔/스코어링 중...")
        self._summary_text.setPlainText("스캔/스코어링 중...")

        self._preview_worker = PreviewWorker(
            list(self._input_dirs),
            recursive=self._inputs.recursive(),
            settings=self._current_grouping(),
            scan_cache=self._scan_cache,
            score_cache=self._score_cache,
            thumbnail_cache=self._thumbnail_cache,
            hash_cache=self._hash_cache,
        )
        grouping = self._current_grouping()
        log.info(
            "미리보기 시작: 폴더 %d개 (%s), 하위 폴더 %s · 간격 %s초 · 나누기 %s · 남길 비율 %s%% · "
            "C컷 기준 선명도 %s·눈뜸 %s",
            len(self._input_dirs), ", ".join(str(d) for d in self._input_dirs),
            "포함" if self._inputs.recursive() else "제외", grouping.gap_seconds,
            grouping.split_distance if grouping.split_distance is not None else "끔",
            grouping.keep_percent, grouping.thresholds.min_sharpness, grouping.thresholds.min_eyes_open,
        )
        self._preview_started = time.monotonic()
        self._preview_worker.progress.connect(self._on_progress)
        self._preview_worker.status.connect(self._status_label.setText)
        self._preview_worker.finished_ok.connect(self._on_preview_done)
        self._preview_worker.failed.connect(self._on_error)
        self._preview_worker.cancelled.connect(self._on_preview_cancelled)
        self._cancel_preview_button.setEnabled(True)
        self._preview_worker.start()

    def _on_preview_done(
        self,
        photos: list[PhotoMetadata],
        rows: list[ReportRow],
        grouping: _SelectionSettings,
        hashes: dict[Path, int | None],
    ) -> None:
        self._busy = False
        self._cancel_preview_button.setEnabled(False)
        self._status_label.setText("완료")
        log.info(
            "미리보기 끝: 사진 %d장, 장면 %d개, 점수 실패 %d장, %.1f초",
            len(rows), len({r.scene_id for r in rows}), sum(1 for r in rows if _is_unscored(r)),
            time.monotonic() - getattr(self, "_preview_started", time.monotonic()),
        )
        score_store.save()
        self._forget_move_snapshot()
        self._table_rows = None  # 새 미리보기는 표를 처음부터(썸네일도 새로)
        self._photos = photos
        self._hashes = hashes
        self._preview_button.setEnabled(True)
        # 지난번(앱을 끄기 전 포함)에 직접 고른 것을 불러온다. 이번 세션에서 고른 것이 우선.
        restored = choice_store.restore(p.path for p in photos if p.path not in self._choices)
        self._choices.update(restored)
        restored_edits = choice_store.scene_edits.restore(
            p.path for p in photos if p.path not in self._scene_edits
        )
        self._scene_edits.update(restored_edits)  # 미리보기 기준과 달라져 끝난 뒤 다시 묶인다
        self._show_rows(rows, grouping)
        if restored:
            self._status_label.setText(f"완료 · 지난번에 직접 고른 {len(restored)}장을 불러왔습니다")
        # 미리보기가 도는 사이에 묶는 기준을 바꿨으면, 끝난 뒤 새 기준으로 다시 묶는다.
        if grouping != self._current_grouping():
            self._regroup_timer.start()

    def _current_grouping(self) -> _SelectionSettings:
        split = self._split_spin.value() if self._split_checkbox.isChecked() else None
        thresholds = BadCutThresholds(
            min_sharpness=self._sharpness_threshold, min_eyes_open=self._eyes_threshold
        )
        return _SelectionSettings(
            self._gap_spin.value(), split, self._keep_spin.value(), thresholds, offset_store().as_key(),
            tuple(sorted(self._scene_edits.items(), key=lambda item: str(item[0]))),
        )

    def _show_rows(self, rows: list[ReportRow], grouping: _SelectionSettings) -> None:
        """새 자동 선정 결과(미리보기 또는 다시 묶기)를 받아 화면에 반영한다."""
        previous = self._rows_grouping
        self._auto_rows = rows
        self._rows_grouping = grouping
        # 장면 묶음이 바뀌었으면(간격·나누기) 열려 있던 크게 보기 창(한 장면의 컷 목록)은 닫는다.
        # 남길 비율·C컷 기준만 바뀌면 장면은 그대로라 창을 둔다(창에서 기준을 지정할 때 등).
        if previous is None or _scene_key(previous) != _scene_key(grouping):
            self._close_viewer()
        self._distances = distances_to_previous(
            shift_photos(self._photos or [], dict(grouping.clock_offsets)), self._hashes
        )
        self._refresh_selection()

    def _refresh_selection(self) -> None:
        """자동 결과에 직접 고른 것을 덮어써 화면·적용 대상을 갱신한다."""
        selected_row = self._selected_table_row()
        rows = apply_manual_choices(self._auto_rows or [], self._choices)
        self._rows = rows
        self._update_apply_buttons()
        settings = self._rows_grouping
        summary = (
            format_preview_header(rows, *settings[:3]) if settings else format_preview_header(rows)
        )
        if settings and settings.clock_offsets:
            summary += f" · 바디 시계 {len(settings.clock_offsets)}대 보정"
        edits_here = self._scene_edits_in(rows)
        if edits_here:
            summary += f" · 장면 직접 고침 {len(edits_here)}곳"
        badcut = format_badcut_summary(rows, self._current_grouping().thresholds)
        manual = format_manual_choice_summary(rows)
        self._summary_text.setPlainText(
            "\n".join(line for line in (summary + (f" · {badcut}" if badcut else ""), manual) if line)
        )
        self._populate_table(rows)
        if selected_row is not None and selected_row < len(rows):
            self._select_index(selected_row)
        self._update_choice_buttons()
        self._ranks_by_path = {
            r.path: (rank, count) for r, (rank, count, _f) in zip(rows, _sharpness_ranks(rows))
        }
        if self._viewer is not None:
            self._viewer.update_rows(self._rows_for_paths(self._viewer_paths))
        if self._compare is not None:
            self._compare.update_rows(self._rows_for_paths(self._compare.paths))

    def _selected_table_row(self) -> int | None:
        """고른 사진의 결과 번호(self._rows 안 위치). 표를 정렬해도 같은 사진을 가리킨다."""
        selected = self._table.selectionModel().selectedRows()
        return self._index_at(selected[0].row()) if selected else None

    def _index_at(self, table_row: int) -> int | None:
        item = self._table.item(table_row, 1)
        return item.data(_INDEX_ROLE) if item is not None else None

    def _select_index(self, index: int) -> None:
        """결과 번호로 표의 줄을 고른다(갤러리 클릭 등)."""
        for table_row in range(self._table.rowCount()):
            if self._index_at(table_row) == index:
                self._table.selectRow(table_row)
                return

    def _update_apply_buttons(self) -> None:
        rows = self._rows or []
        kept = sum(1 for r in rows if r.selected)
        bad = sum(1 for r in rows if _is_badcut(r))
        verb = "이동" if self._move_radio.isChecked() else "복사"
        self._apply_button.setText(f"베스트컷 {verb} ({kept}장)" if rows else f"베스트컷 {verb}")
        self._apply_button.setEnabled(kept > 0 and self._output_dir is not None and not self._busy)
        self._badcut_apply_button.setText(f"C컷 이동 ({bad}장)" if rows else "C컷 이동")
        self._badcut_apply_button.setEnabled(bad > 0 and self._badcut_dir is not None and not self._busy)
        fit_text(self._apply_button)
        fit_text(self._badcut_apply_button, padding=24)
        self._xmp_button.setEnabled(bool(rows) and not self._busy)

    def _update_choice_buttons(self) -> None:
        index = self._selected_table_row()
        row = self._rows[index] if self._rows and index is not None else None
        self._view_button.setEnabled(row is not None)
        self._compare_button.setEnabled(row is not None)
        self._split_action.setEnabled(row is not None)
        self._join_action.setEnabled(row is not None)
        self._reset_scenes_action.setEnabled(bool(self._scene_edits_in(self._rows or [])))
        self._scene_button.setEnabled(bool(self._rows))
        self._toggle_button.setEnabled(row is not None)
        self._toggle_button.setText("선택에서 빼기" if row is not None and row.selected else "선택에 넣기")
        scored = row is not None and not _is_unscored(row)
        self._sharpness_ref_button.setEnabled(scored)
        self._eyes_ref_button.setEnabled(scored and row.face is not None)
        self._reset_choices_button.setEnabled(
            any(r.manual_choice is not None for r in self._rows or [])
        )

    def _toggle(self, row: ReportRow) -> None:
        """이 컷의 선택 여부를 뒤집는다. 뒤집은 결과가 자동 선정과 같으면 직접 고른
        기록을 지워(자동 그대로), 다시 묶을 때 쓸데없는 기록이 남지 않게 한다."""
        auto_selected = next(r.selected for r in self._auto_rows if r.path == row.path)
        wanted = not row.selected
        if wanted == auto_selected:
            self._choices.pop(row.path, None)
        else:
            self._choices[row.path] = wanted
        self._save_choices()
        self._refresh_selection()

    def _set_selected(self, row: ReportRow, wanted: bool) -> None:
        """이미 원하는 상태면 그대로 둔다(P를 두 번 눌러도 빠지지 않게)."""
        if row.selected != wanted:
            self._toggle(row)

    def _toggle_selected_row(self) -> None:
        index = self._selected_table_row()
        if self._rows and index is not None:
            self._toggle(self._rows[index])

    def _reset_choices(self) -> None:
        self._choices.clear()
        self._save_choices()
        self._refresh_selection()

    def _save_choices(self) -> None:
        """직접 고른 것을 앱을 꺼도 남게 저장한다(지금 화면의 사진 기준, choice_store)."""
        choice_store.sync([r.path for r in self._auto_rows or []], self._choices)

    def _rows_for_paths(self, paths: list[Path]) -> list[ReportRow]:
        by_path = {r.path: r for r in self._rows or []}
        return [by_path[p] for p in paths if p in by_path]

    def _open_viewer_for_selected(self) -> None:
        index = self._selected_table_row()
        if index is not None:
            self._open_viewer(index)

    def _open_viewer(self, index: int | None) -> None:
        """클릭한 사진이 속한 장면의 컷만 모아 크게 보기 창을 연다(←/→로 장면 안에서 비교)."""
        if not self._rows or index is None:
            return
        clicked = self._rows[index]
        scene_rows = [r for r in self._rows if r.scene_id == clicked.scene_id]
        self._viewer_paths = [r.path for r in scene_rows]
        self._close_viewer()
        self._viewer = PhotoViewerDialog(
            scene_rows,
            scene_rows.index(clicked),
            actions=[
                ViewerAction(
                    label=lambda row: "이 컷 선택에서 빼기" if row.selected else "이 컷 선택에 넣기",
                    tooltip=(
                        "<p>이 컷을 선택에 넣거나 뺍니다. 한 장면에서 여러 컷을 넣어도 되고, "
                        "자동으로 고른 컷을 빼도 됩니다.</p>"
                    ),
                    enabled=lambda row: True,
                    triggered=self._toggle,
                    shortcut="Space",
                    shortcut_help="넣기/빼기",
                ),
                ViewerAction(
                    label=lambda row: "이 사진을 선명도 기준으로",
                    tooltip="<p>이 사진의 선명도까지는 남기고, 이보다 흐린 사진을 C컷(블러)으로 봅니다.</p>",
                    enabled=lambda row: not _is_unscored(row),
                    triggered=self._set_sharpness_reference,
                ),
                ViewerAction(
                    label=lambda row: "이 사진을 눈뜸 기준으로",
                    tooltip=(
                        "<p>이 사진의 눈 뜬 정도까지는 남기고, 이보다 눈이 더 감긴 사진을 "
                        "C컷(눈감음)으로 봅니다. 얼굴이 검출된 사진에서만 쓸 수 있습니다.</p>"
                    ),
                    enabled=lambda row: not _is_unscored(row) and row.face is not None,
                    triggered=self._set_eyes_reference,
                ),
            ],
            verdict=_describe_choice,
            parent=self,
            # Lightroom 등 사진 선별 도구에 익숙한 키: P(pick) 넣기, X(reject) 빼기
            shortcuts=[
                ViewerShortcut("P", "넣기", lambda row: self._set_selected(row, True)),
                ViewerShortcut("X", "빼기", lambda row: self._set_selected(row, False)),
            ],
        )
        self._viewer.finished.connect(self._on_viewer_closed)
        self._viewer.show()

    def _close_viewer(self) -> None:
        """크게 보기·나란히 비교 창을 닫는다(장면 묶음이 바뀌어 창의 컷 목록이 맞지 않을 때)."""
        if self._viewer is not None:
            self._viewer.close()
            self._viewer = None
        if self._compare is not None:
            self._compare.close()
            self._compare = None

    def _on_viewer_closed(self) -> None:
        self._viewer = None

    # --- 장면 직접 고치기 -----------------------------------------------------------
    def _scene_edits_in(self, rows: list[ReportRow]) -> dict[Path, str]:
        """지금 결과의 사진에 걸린 직접 고친 경계."""
        return {r.path: self._scene_edits[r.path] for r in rows if r.path in self._scene_edits}

    def _edit_scene_at_selected(self, kind: str) -> None:
        index = self._selected_table_row()
        if index is not None:
            self._edit_scene(index, kind)

    def _edit_scene(self, index: int, kind: str) -> None:
        """SPLIT: 이 사진부터 새 장면. JOIN: 이 사진의 장면을 앞 장면에 붙임. 예전에 반대로 고친
        것이 있으면 그것을 지운다(자동 결과로 돌아감)."""
        if self._busy or not self._rows:
            return
        rows = self._rows
        row = rows[index]
        scene_rows = [r for r in rows if r.scene_id == row.scene_id]
        first = scene_rows[0]
        if kind == SPLIT:
            if row.path == first.path:
                self._status_label.setText("이미 장면의 첫 사진이라 나눌 자리가 없습니다")
                return
            target = row.path
            undo = self._scene_edits.get(target) == JOIN
        else:
            if rows[0].scene_id == row.scene_id:
                self._status_label.setText("첫 장면이라 합칠 앞 장면이 없습니다")
                return
            target = first.path
            undo = self._scene_edits.get(target) == SPLIT
        if undo:
            del self._scene_edits[target]
        else:
            self._scene_edits[target] = kind
        log.info("장면 직접 고치기: %s %s", "되돌림" if undo else kind, target.name)
        self._save_scene_edits()
        self._regroup_timer.stop()
        self._update_grouping_help()
        self._regroup()
        self._select_index(index)
        self._status_label.setText(
            "장면을 나눴습니다" if kind == SPLIT else "앞 장면과 합쳤습니다"
        )

    def _reset_scene_edits(self) -> None:
        current = self._scene_edits_in(self._rows or [])
        for path in current:
            del self._scene_edits[path]
        self._save_scene_edits()
        self._regroup_timer.stop()
        self._regroup()
        self._status_label.setText(f"직접 고친 장면 {len(current)}곳을 자동으로 되돌렸습니다")

    def _save_scene_edits(self) -> None:
        choice_store.scene_edits.sync([r.path for r in self._auto_rows or []], self._scene_edits)

    def _on_table_context_menu(self, pos) -> None:
        table_row = self._table.rowAt(pos.y())
        index = self._index_at(table_row) if table_row >= 0 else None
        if index is not None:
            self._select_index(index)
            self._show_context_menu(index, self._table.viewport().mapToGlobal(pos))

    def _show_context_menu(self, index: int, global_pos) -> None:
        """오른쪽 클릭 메뉴: 넣기/빼기, 크게 보기, 나란히 비교, 장면 나누기/합치기."""
        if not self._rows:
            return
        row = self._rows[index]
        menu = QMenu(self)
        menu.addAction("선택에서 빼기 (Space)" if row.selected else "선택에 넣기 (Space)", lambda: self._toggle(row))
        menu.addAction("크게 보기 (Enter)", lambda: self._open_viewer(index))
        menu.addAction("나란히 비교 (C)", lambda: self._open_compare(index))
        menu.addSeparator()
        menu.addAction("여기서 나누기 (S)", lambda: self._edit_scene(index, SPLIT))
        menu.addAction("앞 장면과 합치기 (M)", lambda: self._edit_scene(index, JOIN))
        menu.exec(global_pos)

    # --- 나란히 비교 ---------------------------------------------------------------
    def _open_compare_for_selected(self) -> None:
        index = self._selected_table_row()
        if index is not None:
            self._open_compare(index)

    def _open_compare(self, index: int | None) -> None:
        """고른 사진이 속한 장면의 컷을 나란히 놓고 비교하는 창을 연다(고른 컷부터)."""
        if not self._rows or index is None:
            return
        clicked = self._rows[index]
        scene_rows = [r for r in self._rows if r.scene_id == clicked.scene_id]
        if self._compare is not None:
            self._compare.close()
        self._compare = CompareDialog(
            scene_rows,
            scene_rows.index(clicked),
            state=_compare_state,
            describe=self._describe_for_compare,
            on_toggle=self._toggle,
            on_choose=self._set_selected,
            parent=self,
        )
        self._compare.finished.connect(self._on_compare_closed)
        self._compare.show()

    def _on_compare_closed(self) -> None:
        self._compare = None

    def _describe_for_compare(self, row: ReportRow) -> str:
        if _is_unscored(row):
            return "점수 계산 실패"
        rank, count = self._ranks_by_path.get(row.path, (0, 0))
        eyes = "눈뜸 없음" if row.face is None else f"눈뜸 {row.face:.2f}"
        sharpness = f"선명도 장면 안 {rank}위/{count}" if rank else "선명도 —"
        return f"종합 {row.total:.2f} · {sharpness} · {eyes}"

    # --- C컷 기준 ---------------------------------------------------------------
    def _on_sharpness_spin_changed(self, value: float) -> None:
        self._sharpness_threshold = value
        self._sharpness_ref_name = None
        self._on_threshold_changed()

    def _on_eyes_spin_changed(self, value: float) -> None:
        self._eyes_threshold = value
        self._eyes_ref_name = None
        self._on_threshold_changed()

    def _set_sharpness_reference(self, row: ReportRow) -> None:
        self._sharpness_threshold = row.sharpness
        self._sharpness_ref_name = row.path.name
        # 스핀박스는 보여주기만 갱신한다 — valueChanged가 불리면 기준 사진 지정이 풀리고
        # 반올림된 값이 기준이 돼버린다.
        self._min_sharpness_spin.blockSignals(True)
        self._min_sharpness_spin.setValue(row.sharpness)
        self._min_sharpness_spin.blockSignals(False)
        self._on_threshold_changed()

    def _set_eyes_reference(self, row: ReportRow) -> None:
        if row.face is None:
            return
        self._eyes_threshold = row.face
        self._eyes_ref_name = row.path.name
        self._min_eyes_open_spin.blockSignals(True)
        self._min_eyes_open_spin.setValue(row.face)
        self._min_eyes_open_spin.blockSignals(False)
        self._on_threshold_changed()

    def _use_selected_as_sharpness_reference(self) -> None:
        index = self._selected_table_row()
        if self._rows and index is not None:
            self._set_sharpness_reference(self._rows[index])

    def _use_selected_as_eyes_reference(self) -> None:
        index = self._selected_table_row()
        if self._rows and index is not None:
            self._set_eyes_reference(self._rows[index])

    def _update_reference_labels(self) -> None:
        self._sharpness_ref_label.set_text(
            format_threshold_reference(self._sharpness_ref_name, self._sharpness_threshold, 1)
        )
        self._eyes_ref_label.set_text(
            format_threshold_reference(self._eyes_ref_name, self._eyes_threshold, 2)
        )

    def _on_threshold_changed(self) -> None:
        """C컷 기준이 바뀌면 캐시된 점수로 즉시 다시 판정하고 다시 고른다(스캔 없음)."""
        self._update_reference_labels()
        self._update_guide()
        self._on_grouping_changed()

    # --- C컷 이동 ---------------------------------------------------------------
    def _run_badcut_apply(self) -> None:
        if self._busy:
            QMessageBox.warning(
                self, "작업 진행 중", "다른 작업이 진행 중입니다. 완료될 때까지 기다려주세요."
            )
            return
        bad_rows = [r for r in self._rows or [] if _is_badcut(r)]
        if not bad_rows or self._badcut_dir is None:
            return
        confirm = QMessageBox.question(
            self,
            "C컷 이동 확인",
            f"C컷 {len(bad_rows)}장을 다음 폴더로 옮깁니다(원본 폴더에서 빠지고, 같은 이름의 "
            f"RAW도 함께 옮깁니다):\n{self._badcut_dir}\n\n직접 넣은 컷은 옮기지 않습니다. "
            "계속하시겠습니까?",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        # 옮길 사진만 selected=True로 — apply_selection은 selected인 줄만 옮긴다.
        # 리포트에는 판정 전체(C컷 여부와 사유)를 남긴다.
        move_rows = [replace(r, selected=_is_badcut(r)) for r in self._rows]
        self._busy = True
        self._update_apply_buttons()
        self._update_undo_button()
        self._preview_button.setEnabled(False)
        self._progress_bar.setValue(0)
        self._status_label.setText("C컷 이동 중...")
        self._badcut_worker = ApplyWorker(move_rows, self._badcut_dir, move=True, kind="badcut")
        self._badcut_worker.progress.connect(self._on_progress)
        self._badcut_worker.finished_ok.connect(self._on_badcut_done)
        self._badcut_worker.failed.connect(self._on_badcut_failed)
        self._badcut_worker.start()

    def _on_badcut_done(self, results: list[FileOpResult], report_path: Path) -> None:
        self._busy = False
        self._preview_button.setEnabled(True)
        moved = {r.source for r in results}
        # 옮긴 사진을 빼고 남은 사진으로 다시 묶는다(점수는 캐시라 즉시)
        self._remember_move("badcut", results, self._badcut_dir)
        self._regroup()
        self._status_label.setText(f"C컷 {len(moved)}장 이동 완료")
        if self._rows is not None:
            self._summary_text.setPlainText(
                f"C컷 {len(moved)}장 이동 완료 → {self._badcut_dir}\n판정 리포트: {report_path}\n"
                + self._summary_text.toPlainText()
            )

    def _on_badcut_failed(self, message: str, results: list[FileOpResult]) -> None:
        # 일부만 옮기고 실패했어도 옮긴 것은 되돌릴 수 있게 기억하고, 남은 사진으로 다시 묶는다
        self._remember_move("badcut", results, self._badcut_dir)
        self._on_error(message)
        self._regroup()

    def _on_apply_failed(self, message: str, results: list[FileOpResult]) -> None:
        self._remember_move("bestcut", results, self._output_dir)
        self._on_error(message)

    # --- 방금 이동 되돌리기 -------------------------------------------------------
    def _remember_move(self, kind: str, results: list[FileOpResult], log_dir: Path | None) -> None:
        """옮긴 사진을 미리보기에서 빼고, 되돌릴 수 있게 기억한다(이동이 아니면 아무것도 안 함)."""
        moved = {r.source for r in results if r.action == "move"}
        if not moved or log_dir is None:
            return
        photos_before = self._photos
        choices_before = dict(self._choices)
        if kind == "badcut":
            if self._photos is not None:
                self._photos = [p for p in self._photos if p.path not in moved]
            for path in moved:
                self._choices.pop(path, None)
            choice_store.forget(moved)
            self._rows_grouping = None
        else:
            # 옮긴 파일은 더 이상 입력 폴더에 없어, 간격을 바꿔 다시 묶을 수 없다.
            self._photos = None
        self._last_move = _LastMove(
            kind, results, log_dir, photos_before, self._photos, choices_before
        )
        self._last_xmp = None  # 되돌리기는 가장 최근 작업 하나만
        self._update_undo_button()

    def _update_undo_button(self) -> None:
        move = self._last_move
        available = move is not None or self._last_xmp is not None
        self._undo_move_button.setVisible(available)
        self._undo_move_button.setEnabled(available and not self._busy)
        if move is not None:
            label = "C컷" if move.kind == "badcut" else "베스트컷"
            count = sum(1 for r in move.results if r.action == "move")
            self._undo_move_button.setText(f"↶ {label} 되돌리기 ({count}장)")
            fit_text(self._undo_move_button, padding=24)
        elif self._last_xmp is not None:
            count = sum(1 for r in self._last_xmp[0] if r.action != ACTION_SKIPPED)
            self._undo_move_button.setText(f"↶ 별점 되돌리기 ({count}개)")
            fit_text(self._undo_move_button, padding=24)

    def _undo_last_move(self) -> None:
        if self._last_xmp is not None and self._last_move is None and not self._busy:
            self._undo_last_xmp()
            return
        move = self._last_move
        if self._busy or move is None:
            return
        label = "C컷" if move.kind == "badcut" else "베스트컷"
        count = sum(1 for r in move.results if r.action == "move")
        confirm = QMessageBox.question(
            self,
            "이동 되돌리기",
            f"방금 옮긴 {label} {count}장(같이 옮긴 RAW 포함)을 원래 폴더로 되돌립니다.\n"
            f"{move.log_dir} → 원래 폴더\n\n원래 자리에 같은 이름의 파일이 새로 생겼으면 그 "
            "사진은 덮어쓰지 않고 그대로 둡니다. 계속하시겠습니까?",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self._busy = True
        self._update_apply_buttons()
        self._update_undo_button()
        self._preview_button.setEnabled(False)
        self._status_label.setText("원래 폴더로 되돌리는 중...")
        self._undo_worker = UndoWorker(move.results, move.log_dir)
        self._undo_worker.finished_ok.connect(self._on_undo_done)
        self._undo_worker.failed.connect(self._on_undo_failed)
        self._undo_worker.start()

    def _on_undo_done(self, outcome: UndoOutcome) -> None:
        move = self._last_move
        self._last_move = None
        self._busy = False
        self._preview_button.setEnabled(True)
        self._update_undo_button()
        restored = {r.source for r in outcome.restored}
        if move is not None and self._photos is move.photos_after and move.photos_before is not None:
            # 옮긴 뒤로 새로 미리보기하지 않았으면, 되돌린 사진을 다시 넣어 바로 다시 묶는다.
            moved = {r.source for r in move.results if r.action == "move"}
            self._photos = [
                p for p in move.photos_before if p.path not in moved or p.path in restored
            ]
            for path in restored:
                if path in move.choices_before:
                    self._choices[path] = move.choices_before[path]
            self._rows_grouping = None
            self._regroup()
            self._save_choices()
            note = ""
        else:
            note = " — 다시 미리보기하면 되돌린 사진도 함께 보입니다"
        message = f"{len(restored)}장을 원래 폴더로 되돌렸습니다{note}"
        self._status_label.setText(message)
        self._update_apply_buttons()
        if outcome.skipped:
            lines = "\n".join(f"- {path.name}: {why}" for path, why in outcome.skipped[:10])
            more = f"\n…외 {len(outcome.skipped) - 10}개" if len(outcome.skipped) > 10 else ""
            QMessageBox.warning(
                self,
                "일부는 되돌리지 못함",
                f"{len(outcome.skipped)}개 파일은 되돌리지 못하고 그대로 두었습니다:\n{lines}{more}",
            )

    def _on_undo_failed(self, message: str) -> None:
        # 어디까지 되돌렸는지 알 수 없어 다시 시도하지 않게 잊는다(기록은 undo_log에 있음)
        self._last_move = None
        self._update_undo_button()
        self._on_error(message)

    # --- 라이트룸 별점(.xmp) ------------------------------------------------------------
    def _run_xmp(self) -> None:
        if self._busy or not self._rows:
            return
        rating = self._xmp_rating_spin.value()
        best = [r.path for r in self._rows if r.selected]
        rejects = [r.path for r in self._rows if _is_badcut(r)]
        plan = plan_xmp(best, rejects)
        with_raw = [row for row in plan if row.xmp is not None]
        if not with_raw:
            QMessageBox.information(
                self,
                "라이트룸 별점",
                "베스트컷·C컷 중 RAW가 있는 사진이 없습니다. 라이트룸은 RAW 옆의 .xmp만 읽어서 "
                "JPG만 있는 사진에는 별점을 쓸 수 없습니다.",
            )
            return
        best_count = sum(1 for row in with_raw if row.mark == "best")
        reject_count = len(with_raw) - best_count
        existing = sum(1 for row in with_raw if row.existing)
        no_raw = len(plan) - len(with_raw)
        lines = [f"베스트컷 {best_count}장에 {'★' * rating}, C컷 {reject_count}장에 거부 표시를 "
                 "RAW 옆 .xmp에 씁니다(RAW·JPG 파일은 그대로)."]
        lines.append(f"새로 만들기 {len(with_raw) - existing}개 · 기존 .xmp 고치기 {existing}개"
                     "(별점·거부 칸만, 고치기 전 파일은 백업)")
        if no_raw:
            lines.append(f"RAW가 없어 건너뜀 {no_raw}장 (JPG만 있는 사진)")
        confirm = QMessageBox.question(self, "라이트룸 별점 쓰기", "\n".join(lines) + "\n\n계속하시겠습니까?")
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self._busy = True
        self._update_apply_buttons()
        self._update_undo_button()
        self._status_label.setText("라이트룸 별점 쓰는 중...")
        run_dir = default_run_dir(score_store.cache_dir().parent)
        self._xmp_worker = XmpWorker(plan, rating, run_dir)
        self._xmp_worker.finished_ok.connect(lambda results: self._on_xmp_done(results, run_dir))
        self._xmp_worker.failed.connect(self._on_error)
        self._xmp_worker.start()

    def _on_xmp_done(self, results: list[XmpResult], run_dir: Path) -> None:
        self._busy = False
        self._preview_button.setEnabled(True)
        created = sum(1 for r in results if r.action == ACTION_CREATED)
        updated = sum(1 for r in results if r.action == ACTION_UPDATED)
        skipped = [r for r in results if r.action == ACTION_SKIPPED]
        if created or updated:
            self._last_xmp = (results, run_dir)
            self._last_move = None  # 되돌리기는 가장 최근 작업 하나만
        self._update_apply_buttons()
        self._update_undo_button()
        self._status_label.setText(f"라이트룸 별점: 만듦 {created} · 고침 {updated} · 건너뜀 {len(skipped)}")
        self._summary_text.setPlainText(
            f"라이트룸 별점: .xmp 새로 만듦 {created}개 · 고침 {updated}개 · 건너뜀 {len(skipped)}장\n"
            f"백업·처리 기록: {run_dir}\n"
            "라이트룸에 이미 가져온 사진은 메타데이터 → 파일에서 메타데이터 읽기를 해야 반영됩니다.\n"
            + self._summary_text.toPlainText()
        )
        unsafe = [r for r in skipped if r.plan.xmp is not None]
        if unsafe:
            lines = "\n".join(f"- {r.plan.xmp.name}: {r.reason}" for r in unsafe[:10])
            QMessageBox.warning(self, "일부 .xmp는 건드리지 않음", f"{len(unsafe)}개는 그대로 두었습니다:\n{lines}")

    def _undo_last_xmp(self) -> None:
        results, run_dir = self._last_xmp
        count = sum(1 for r in results if r.action != ACTION_SKIPPED)
        confirm = QMessageBox.question(
            self,
            "별점 되돌리기",
            f"방금 쓴 라이트룸 별점 {count}개를 되돌립니다: 새로 만든 .xmp는 지우고, 고친 .xmp는 "
            f"백업({run_dir})으로 되돌립니다. 그 뒤로 내용이 바뀐 파일은 그대로 둡니다. 계속하시겠습니까?",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        restored, skipped = undo_xmp(results)
        self._last_xmp = None
        self._update_undo_button()
        self._status_label.setText(f"별점 {restored}개를 되돌렸습니다")
        if skipped:
            lines = "\n".join(f"- {path.name}: {why}" for path, why in skipped[:10])
            QMessageBox.warning(self, "일부는 되돌리지 못함", f"{len(skipped)}개는 그대로 두었습니다:\n{lines}")

    def _on_gap_changed(self, value: float) -> None:
        save_float(self._settings, SCENE_GAP_SETTINGS_KEY, value)
        self._on_grouping_changed()

    def _on_split_changed(self) -> None:
        save_bool(self._settings, SPLIT_ENABLED_SETTINGS_KEY, self._split_checkbox.isChecked())
        save_float(self._settings, SPLIT_DISTANCE_SETTINGS_KEY, self._split_spin.value())
        self._split_spin.setEnabled(self._split_checkbox.isChecked())
        self._on_grouping_changed()

    def _on_keep_changed(self, value: int) -> None:
        save_float(self._settings, KEEP_PERCENT_SETTINGS_KEY, value)
        self._on_grouping_changed()

    def _open_clock_sync(self) -> None:
        if not self._input_dirs:
            QMessageBox.warning(self, "입력 폴더 없음", "먼저 입력 폴더를 하나 이상 추가하세요.")
            return
        ClockSyncDialog(
            list(self._input_dirs), self._inputs.recursive(), self._scan_cache, parent=self
        ).exec()

    def _update_clock_status(self) -> None:
        text, tooltip = describe_offsets(offset_store().get())
        self._clock_status.setText(text)
        self._clock_status.setToolTip(tooltip)

    def _on_clock_changed(self) -> None:
        self._update_clock_status()
        self._on_grouping_changed()

    def _on_grouping_changed(self) -> None:
        self._update_grouping_help()
        # 아직 미리보기 전이면 다음 미리보기 때 이 기준으로 묶인다.
        if self._photos is not None:
            self._regroup_timer.start()

    def _regroup(self) -> None:
        """캐시된 점수로 새 간격에 맞춰 장면을 다시 묶고 베스트컷을 다시 고른다.
        스캔·스코어링을 다시 하지 않아 즉시 끝난다."""
        if self._busy:
            # 미리보기/적용이 끝나면 _on_preview_done이 간격 차이를 보고 다시 부른다.
            return
        if self._photos is None:
            return
        grouping = self._current_grouping()
        if grouping == self._rows_grouping:
            return
        try:
            groups = _make_groups(
                self._photos, self._hashes, grouping.gap_seconds, grouping.split_distance,
                grouping.clock_offsets, grouping.scene_edits,
            )
            rows = build_report(
                groups,
                cache=self._score_cache,
                keep_percent=grouping.keep_percent,
                exclude=_exclude_for(grouping.thresholds),
            )
        except OSError as e:
            # 미리보기 뒤에 파일이 옮겨졌거나 지워진 경우 — 새로 미리보기해야 한다.
            self._invalidate_preview()
            QMessageBox.warning(
                self,
                "다시 미리보기 필요",
                f"미리보기 뒤에 사진 파일이 바뀌어 다시 묶을 수 없습니다. 미리보기를 다시 "
                f"눌러주세요.\n({e})",
            )
            return
        self._show_rows(rows, grouping)
        self._status_label.setText("바뀐 기준으로 다시 묶고 다시 골랐습니다")

    def _update_grouping_help(self) -> None:
        settings = self._current_grouping()
        scoring_help = bestcut_scoring_help(keep_percent=settings.keep_percent)
        self._scoring_help_label.setToolTip(scoring_help)
        self._table.horizontalHeaderItem(4).setToolTip(scoring_help)
        self._table.horizontalHeaderItem(9).setToolTip(scoring_help)
        for widget in self._keep_widgets:
            widget.setToolTip(scoring_help)
        self._keep_hint.setText(
            "(장면마다 1장)"
            if not settings.keep_percent
            else "(장면 사진 수 × 비율, 올림, 최소 1장)"
        )
        help_text = bestcut_grouping_help(settings.gap_seconds, settings.split_distance)
        for widget in (*self._gap_widgets, self._split_checkbox, self._split_spin):
            widget.setToolTip(help_text + _DIFF_TOOLTIP)
        self._grouping_help_label.setToolTip(help_text)
        self._table.horizontalHeaderItem(2).setToolTip(help_text)

    def _populate_table(self, rows: list[ReportRow]) -> None:
        split = self._rows_grouping.split_distance if self._rows_grouping else None
        previous = self._table_rows
        if (
            previous is not None
            and len(previous) == len(rows)
            and self._table_split == split
            and all(a.path == b.path for a, b in zip(previous, rows))
        ):
            # 같은 사진·같은 순서(넣기/빼기, C컷 기준 변경 등): 바뀐 줄만 다시 채운다 — 수천 장에서
            # 표 전체를 다시 만들면 Space 한 번에 0.5초 넘게 걸렸다.
            changed = [i for i, (a, b) in enumerate(zip(previous, rows)) if a != b]
            if changed:
                ranks = _sharpness_ranks(rows)
                table_row_of = {self._index_at(t): t for t in range(self._table.rowCount())}
                self._table.setSortingEnabled(False)
                for i in changed:
                    self._fill_table_row(table_row_of[i], i, rows[i], ranks[i])
                self._table.setSortingEnabled(True)
        else:
            sharpness_ranks = _sharpness_ranks(rows)
            # 채우는 도중에 정렬이 켜져 있으면 setItem마다 줄이 재배치돼 칸이 뒤섞인다.
            self._table.setSortingEnabled(False)
            self._table.setRowCount(len(rows))
            for i, row in enumerate(rows):
                self._fill_table_row(i, i, row, sharpness_ranks[i])
            self._table.setSortingEnabled(True)
            self._table.resizeRowsToContents()
        self._table_rows = rows
        self._table_split = split
        self._apply_row_filter()
        self._populate_gallery(rows)
        self._show_results()

    def _fill_table_row(
        self, table_row: int, i: int, row: ReportRow, rank_info: tuple[int, int, float]
    ) -> None:
        """표의 table_row 줄을 결과 i번째 사진(row)으로 채운다(정렬돼 있으면 둘이 다를 수 있음)."""
        thumb_item = QTableWidgetItem()
        pixmap = self._thumbnail_pixmap(row.path)
        if pixmap is not None:
            thumb_item.setData(Qt.ItemDataRole.DecorationRole, pixmap)
        self._table.setItem(table_row, 0, thumb_item)
        name_item = _SortItem(row.path.name, i)  # 파일명 정렬은 촬영 순서와 같게
        name_item.setData(_INDEX_ROLE, i)
        self._table.setItem(table_row, 1, name_item)
        self._table.setItem(table_row, 2, _SortItem(str(row.scene_id), i))  # 장면 = 원래 순서
        self._table.setItem(table_row, 3, QTableWidgetItem(self._difference_text(row)))
        badcut = _is_badcut(row)
        if row.selected:
            choice, order = "✓ 선택", 0
        elif badcut:
            choice, order = "C컷", 2
        else:
            choice, order = "제외", 1
        if row.manual_choice is not None:
            choice += " (직접)"
        choice_item = _SortItem(choice, (order, i))
        if badcut:
            choice_item.setToolTip(f"C컷: {row.badcut_reason}")
        self._table.setItem(table_row, 4, choice_item)
        unscored = _is_unscored(row)
        self._table.setItem(table_row, 5, _bar_item(f"{row.total:.2f}", None if unscored else row.total))
        rank, count, fraction = rank_info
        # 보이는 것은 장면 안 순위, 정렬은 원래 값(C컷 기준 사진을 고를 때 쓰는 순서)
        sharpness_item = _bar_item(
            "–" if unscored else f"{rank}위 / {count}장",
            None if unscored else fraction,
            sort_key=None if unscored else row.sharpness,
        )
        region = format_sharpness_region(row.sharpness_region)
        sharpness_item.setToolTip(f"선명도 원래 값 {row.sharpness:.1f}" + (f" ({region})" if region else ""))
        self._table.setItem(table_row, 6, sharpness_item)
        self._table.setItem(table_row, 7, _bar_item(f"{row.exposure:.2f}", None if unscored else row.exposure))
        self._table.setItem(
            table_row, 8, _bar_item("" if row.face is None else f"{row.face:.2f}", row.face)
        )
        self._table.setItem(table_row, 9, QTableWidgetItem(row.reason))
        # 남기는 컷은 초록, C컷은 빨강, 나머지는 장면을 하나 걸러 옅은 회색으로 장면 경계를
        if row.selected:
            paint_row(self._table, table_row, KEPT_ROW, bold_column=4)
        elif badcut:
            paint_row(self._table, table_row, FLAGGED_ROW, bold_column=4)
        else:
            paint_row(self._table, table_row, SCENE_BAND if row.scene_id % 2 == 0 else None)

    def _apply_row_filter(self) -> None:
        """'남기는 컷만 보기'면 남기지 않는 줄을 숨긴다(정렬로 줄이 바뀌어도 다시 맞춤)."""
        rows = self._rows or []
        only_kept = self._only_kept()
        for table_row in range(self._table.rowCount()):
            index = self._index_at(table_row)
            hide = only_kept and index is not None and index < len(rows) and not rows[index].selected
            self._table.setRowHidden(table_row, hide)

    def _difference_text(self, row: ReportRow) -> str:
        """앞 사진과 차이 칸. 나누기를 켠 상태에서 이 차이 때문에 장면이 나뉜 자리는 ✂."""
        distance = self._distances.get(row.path)
        if distance is None:
            return ""
        split_distance = self._rows_grouping.split_distance if self._rows_grouping else None
        if split_distance is not None and distance > split_distance:
            return f"{distance} ✂"
        return str(distance)

    def _thumbnail_pixmap(self, path: Path, size: int = TABLE_ICON) -> QPixmap | None:
        cached = self._thumbnail_cache.get(path)
        if cached is None or cached[2] is None:
            return None
        key = cached[:2]
        entry = self._pixmap_cache.get((path, size))
        if entry is not None and entry[0] == key:
            return entry[1]
        pixmap = QPixmap()
        if not pixmap.loadFromData(cached[2], "JPEG"):
            return None
        if pixmap.width() > size or pixmap.height() > size:
            pixmap = pixmap.scaled(
                size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
        self._pixmap_cache[(path, size)] = (key, pixmap)
        return pixmap

    def _populate_gallery(self, rows: list[ReportRow]) -> None:
        only_kept = self._only_kept()
        by_scene: dict[int, list[GalleryPhoto]] = {}
        for i, row in enumerate(rows):
            photos = by_scene.setdefault(row.scene_id, [])
            if not only_kept or row.selected:
                photos.append(
                    GalleryPhoto(
                        i, row.path, row.selected, row.manual_choice is not None, row.total, _is_badcut(row)
                    )
                )
        # 장면 순서는 그대로, 남기는 컷만 볼 때 빈 장면은 뺀다
        edited_scenes = {row.scene_id for row in rows if row.path in self._scene_edits}
        scenes = [
            GalleryScene(scene_id, tuple(photos), scene_id in edited_scenes)
            for scene_id, photos in by_scene.items()
            if photos
        ]
        self._gallery.set_scenes(scenes)
        self._gallery.set_current(self._selected_table_row(), scroll=False)

    def _on_collapse_toggled(self, collapsed: bool) -> None:
        self._settings_box.setVisible(not collapsed)
        self._collapse_button.setText("▸ 설정 펴기" if collapsed else "▾ 설정 접기")
        save_bool(self._settings, SETTINGS_COLLAPSED_KEY, collapsed)

    def _on_view_toggled(self, gallery: bool) -> None:
        self._settings.setValue(VIEW_SETTINGS_KEY, "gallery" if gallery else "table")
        self._show_results()

    def _show_results(self) -> None:
        """결과가 있으면 고른 보기(갤러리/표), 없으면 순서 안내."""
        if not hasattr(self, "_guide"):
            return  # 생성 중(보기 버튼을 처음 켤 때)
        if not self._rows:
            self._update_guide()
            self._results.setCurrentWidget(self._guide)
        else:
            self._results.setCurrentWidget(self._gallery if self._gallery_button.isChecked() else self._table)

    def _update_guide(self) -> None:
        if not hasattr(self, "_guide"):
            return
        thresholds_set = self._sharpness_threshold > 0 or self._eyes_threshold > 0
        self._guide.set_done(
            [bool(self._input_dirs), self._output_dir is not None, bool(self._rows), thresholds_set, False]
        )

    def _only_kept(self) -> bool:
        return self._only_kept_checkbox.isChecked()

    def _on_only_kept_toggled(self, _checked: bool) -> None:
        if self._rows:
            self._populate_table(self._rows)

    def _choose_selected_row(self, wanted: bool) -> None:
        index = self._selected_table_row()
        if self._rows and index is not None:
            self._set_selected(self._rows[index], wanted)

    def _choose_row(self, index: int, wanted: bool) -> None:
        self._select_index(index)
        self._choose_selected_row(wanted)

    def _toggle_row(self, index: int) -> None:
        self._select_index(index)
        self._toggle_selected_row()

    def _run_apply(self) -> None:
        if self._busy:
            QMessageBox.warning(
                self, "작업 진행 중", "다른 작업이 진행 중입니다. 완료될 때까지 기다려주세요."
            )
            return

        if not self._rows:
            QMessageBox.warning(self, "미리보기 필요", "먼저 미리보기를 실행하세요.")
            return

        if self._output_dir is None:
            QMessageBox.warning(self, "출력 폴더 없음", "출력 폴더를 먼저 선택하세요.")
            return

        move = self._move_radio.isChecked()
        verb = "이동" if move else "복사"
        selected_count = sum(1 for r in self._rows if r.selected)
        manual = format_manual_choice_summary(self._rows)
        confirm = QMessageBox.question(
            self,
            "베스트컷 " + verb + " 확인",
            f"베스트컷 {selected_count}장" + (f"({manual})" if manual else "")
            + f"을 {self._output_dir}(으)로 {verb}하시겠습니까?",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        self._busy = True
        self._update_apply_buttons()
        self._update_undo_button()
        self._progress_bar.setValue(0)

        self._apply_worker = ApplyWorker(self._rows, self._output_dir, move)
        self._apply_worker.progress.connect(self._on_progress)
        self._apply_worker.finished_ok.connect(self._on_apply_done)
        self._apply_worker.failed.connect(self._on_apply_failed)
        self._apply_worker.start()

    def _on_apply_done(self, results: list[FileOpResult], report_path: Path) -> None:
        self._busy = False
        if self._move_radio.isChecked():
            self._remember_move("bestcut", results, self._output_dir)
        elif self._photos is not None and self._current_grouping() != self._rows_grouping:
            # 적용하는 사이에 묶는 기준을 바꿨으면 이제 다시 묶는다.
            self._regroup_timer.start()
        self._update_apply_buttons()
        self._update_undo_button()
        self._open_output_button.setEnabled(True)
        self._summary_text.setPlainText(
            format_apply_summary(results, self._output_dir, report_path)
        )
        if self._output_dir is not None and results:
            folders = (self._output_dir,)
            self._next_steps.set_steps(
                [NextStep("proof", folders), NextStep("resize", folders), NextStep("rename", folders)],
                label="복사한 베스트컷으로:" if not self._move_radio.isChecked() else "옮긴 베스트컷으로:",
            )

    def _open_output_folder(self) -> None:
        if self._output_dir is not None:
            # macOS/Windows 공통으로 OS 기본 파일 탐색기에서 연다
            opened = QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(self._output_dir))
            )
            if not opened:
                QMessageBox.warning(
                    self,
                    "폴더 열기 실패",
                    f"출력 폴더를 열지 못했습니다: {self._output_dir}",
                )

    def _on_progress(self, done: int, total: int) -> None:
        self._progress_bar.setMaximum(total)
        self._progress_bar.setValue(done)

    def _cancel_preview(self) -> None:
        if self._preview_worker is not None and self._preview_worker.isRunning():
            self._preview_worker.request_cancel()
            self._cancel_preview_button.setEnabled(False)
            self._status_label.setText("취소하는 중...")

    def _on_preview_cancelled(self) -> None:
        self._busy = False
        self._cancel_preview_button.setEnabled(False)
        self._preview_button.setEnabled(True)
        self._progress_bar.setValue(0)
        # 그때까지 계산한 점수는 캐시에 이미 들어가 있다 — 저장해 두면 다음에 이어서 계산된다.
        score_store.save()
        self._status_label.setText("미리보기를 취소했습니다 (그때까지 계산한 점수는 저장됨)")
        log.info("미리보기 취소")
        self._summary_text.setPlainText(
            "미리보기를 취소했습니다. 다시 미리보기를 누르면 이미 계산한 사진은 건너뛰고 이어서 "
            "계산합니다."
        )

    def _on_error(self, message: str) -> None:
        self._cancel_preview_button.setEnabled(False)
        self._busy = False
        self._preview_button.setEnabled(True)
        self._update_apply_buttons()
        self._update_undo_button()
        QMessageBox.critical(self, "오류", message)
