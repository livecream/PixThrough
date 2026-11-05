"""설정 내보내기·가져오기(파일 › 설정 내보내기… / 설정 가져오기…).

컴퓨터를 바꾸거나 다시 설치할 때 쓰도록 앱 설정(QSettings — 폴더·기준·가중치·바디 시차·
라이트룸 별점 등)과 직접 한 기록(직접 고른 컷·장면 직접 고치기·고객이 고른 사진)을 JSON 파일
하나로 주고받는다. 사용자 결정: 점수·유사도 지문 저장소는 넣지 않는다(다시 계산하면 되고 큼).

- 창 크기·위치처럼 그 컴퓨터 화면에만 맞는 값(EXCLUDED_KEYS)과 JSON으로 옮길 수 없는 값은 뺀다.
- 직접 한 기록은 사진 경로·수정 시각·크기로 찾으므로 같은 경로에 같은 사진이 있을 때만 쓰인다
  (다른 컴퓨터에서 경로가 다르면 조용히 쓰이지 않음 — 기존 저장소 규칙 그대로).
- 가져오기는 파일에 있는 설정만 바꾸고(없는 설정은 그대로), 기록은 더한다(같은 사진은 가져온
  것으로). 가져오기 전에 지금 상태를 앱 데이터 폴더 `settings_backup/`에 내보내 둔다.
"""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QSettings

import choice_store
import history
import projects
import score_store
from version import __version__

FORMAT = 1
APP = "PixThrough"
EXCLUDED_KEYS = {"window/geometry"}


@dataclass(frozen=True)
class Summary:
    settings: int
    records: dict[str, int]  # 저장소 이름 → 기록 수

    def describe(self) -> str:
        names = {"choices": "직접 고른 컷", "scene_edits": "직접 고친 장면", "client_picks": "고객이 고른 사진",
                 "ratings": "별점",
                 "projects": "촬영 건"}
        parts = [f"설정 {self.settings}개"]
        parts += [f"{names.get(k, k)} {n}장" for k, n in self.records.items() if n]
        return " · ".join(parts)


def _jsonable(value: object) -> bool:
    if value is None or isinstance(value, (str, bool, int, float)):
        return True
    if isinstance(value, list):
        return all(_jsonable(item) for item in value)
    return False


def collect(settings: QSettings) -> dict:
    values = {}
    for key in settings.allKeys():
        if key in EXCLUDED_KEYS:
            continue
        value = settings.value(key)
        if _jsonable(value):
            values[key] = value
    return {
        "app": APP,
        "format": FORMAT,
        "app_version": __version__,
        "exported": datetime.datetime.now().isoformat(timespec="seconds"),
        "settings": values,
        "records": {name: store.export_entries() for name, store in choice_store.RECORD_STORES.items()},
        "projects": [p.to_json() for p in projects.load()],
    }


def summarize(data: dict) -> Summary:
    settings = data.get("settings") if isinstance(data.get("settings"), dict) else {}
    records = data.get("records") if isinstance(data.get("records"), dict) else {}
    counts = {name: len(records.get(name) or {}) for name in choice_store.RECORD_STORES
              if isinstance(records.get(name) or {}, dict)}
    if isinstance(data.get("projects"), list):
        counts["projects"] = len(data["projects"])
    return Summary(len(settings), counts)


def export_to(settings: QSettings, path: Path, record: bool = True) -> Summary:
    data = collect(settings)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    summary = summarize(data)
    if record:
        history.record("설정 내보내기", summary.describe(), path.parent, path)
    return summary


def read_file(path: Path) -> dict:
    """가져올 파일을 읽어 확인한다. PixThrough 설정 파일이 아니면 ValueError."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except UnicodeDecodeError as e:
        raise ValueError("PixThrough 설정 파일이 아닙니다(글자 파일이 아님).") from e
    except json.JSONDecodeError as e:
        raise ValueError(f"PixThrough 설정 파일이 아닙니다(JSON 형식 오류: {e.msg}).") from e
    if not isinstance(data, dict) or data.get("app") != APP:
        raise ValueError("PixThrough 설정 파일이 아닙니다.")
    if not isinstance(data.get("format"), int) or data["format"] > FORMAT:
        raise ValueError("더 새 버전의 PixThrough에서 내보낸 파일입니다. 앱을 업데이트한 뒤 가져오세요.")
    return data


def _import_projects(items: object) -> int:
    """촬영 건 목록을 더한다(같은 폴더의 촬영 건은 가져온 이름으로)."""
    if not isinstance(items, list):
        return 0
    current = projects.load()
    by_root = {str(p.root): p for p in current}
    added = 0
    for item in items:
        try:
            project = projects.Project.from_json(item)
        except (KeyError, ValueError, TypeError):
            continue
        by_root[str(project.root)] = project
        added += 1
    if added:
        projects.save(list(by_root.values()))
    return added


def backup_dir() -> Path:
    return score_store.cache_dir().parent / "settings_backup"


def import_from(settings: QSettings, path: Path) -> tuple[Summary, Path]:
    """path의 설정·기록을 가져온다. (가져온 개수, 가져오기 전 상태를 내보낸 백업 파일)."""
    data = read_file(path)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = backup_dir() / f"before_import_{stamp}.json"
    export_to(settings, backup, record=False)
    values = data.get("settings") if isinstance(data.get("settings"), dict) else {}
    applied = 0
    for key, value in values.items():
        if isinstance(key, str) and key not in EXCLUDED_KEYS and _jsonable(value):
            settings.setValue(key, value)
            applied += 1
    settings.sync()
    records = data.get("records") if isinstance(data.get("records"), dict) else {}
    added = {name: store.import_entries(records.get(name)) for name, store in choice_store.RECORD_STORES.items()}
    added["projects"] = _import_projects(data.get("projects"))
    summary = Summary(applied, added)
    history.record("설정 가져오기", summary.describe(), Path(path).parent, Path(path))
    return summary, backup
