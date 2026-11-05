"""촬영 건(프로젝트): "2026-11-03 김OO 본식"처럼 촬영 하나를 이름과 사진 폴더(가장 위 폴더)로 기억해,
다시 열면 각 화면에 그 폴더를 넣고 어디까지 했는지(진행 단계)를 보여준다.

진행 단계는 따로 적지 않고 작업 기록(history.jsonl)에서 계산한다 — 그 촬영 건 폴더 안에서 했거나
(결과 폴더가) 그 안에 있는 작업. 그래서 이 기능 이전 작업이나 다른 곳으로 옮긴 결과는 안 보일 수 있다.
목록은 앱 데이터 폴더 `projects.json`(최근 것부터). 지워도 사진 폴더는 그대로다.
"""

from __future__ import annotations

import datetime
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

import history

FILE_NAME = "projects.json"
MAX_PROJECTS = 200

# (단계 이름, 그 단계로 치는 작업 기록 종류, 열 화면)
STEPS = (
    ("가져오기", ("카드 가져오기",), "import"),
    ("시간순 정렬", ("시간순 정렬",), "rename"),
    ("베스트컷", ("베스트컷 복사", "베스트컷 이동", "C컷 이동", "라이트룸 별점", "컨택트 시트"), "selection"),
    ("고객 셀렉", ("고객 셀렉 페이지", "고객 선택 복사", "라이트룸 라벨"), "proof"),
    ("사진 축소", ("사진 축소",), "resize"),
)


@dataclass
class Project:
    id: str
    name: str
    root: Path
    created: datetime.datetime
    opened: datetime.datetime

    def to_json(self) -> dict:
        return {
            "id": self.id, "name": self.name, "root": str(self.root),
            "created": self.created.isoformat(timespec="seconds"),
            "opened": self.opened.isoformat(timespec="seconds"),
        }

    @classmethod
    def from_json(cls, data: dict) -> "Project":
        return cls(
            str(data["id"]), str(data["name"]), Path(data["root"]),
            datetime.datetime.fromisoformat(data["created"]), datetime.datetime.fromisoformat(data["opened"]),
        )


@dataclass(frozen=True)
class StepState:
    name: str
    page: str
    done_at: datetime.datetime | None  # 그 단계 작업을 마지막으로 한 때(안 했으면 None)
    summary: str = ""


def _path() -> Path:
    import app_log

    return app_log.app_data_dir() / FILE_NAME


def load(path: Path | None = None) -> list[Project]:
    """최근에 연 것부터. 깨진 항목은 건너뛴다."""
    try:
        data = json.loads((path or _path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    projects = []
    for item in data.get("projects", []) if isinstance(data, dict) else []:
        try:
            projects.append(Project.from_json(item))
        except (KeyError, ValueError, TypeError):
            continue
    projects.sort(key=lambda p: p.opened, reverse=True)
    return projects


def save(projects: list[Project], path: Path | None = None) -> None:
    path = path or _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    items = sorted(projects, key=lambda p: p.opened, reverse=True)[:MAX_PROJECTS]
    tmp.write_text(json.dumps({"version": 1, "projects": [p.to_json() for p in items]}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    os.replace(tmp, path)


def create(name: str, root: Path, path: Path | None = None) -> Project:
    """새 촬영 건(같은 폴더가 이미 있으면 그 촬영 건 이름만 바꿔 맨 위로)."""
    now = datetime.datetime.now().replace(microsecond=0)
    projects = load(path)
    root = Path(root)
    for project in projects:
        if _same(project.root, root):
            project.name = name.strip() or project.name
            project.opened = now
            save(projects, path)
            return project
    project = Project(uuid.uuid4().hex[:12], name.strip() or root.name, root, now, now)
    save([project, *projects], path)
    return project


def touch(project_id: str, path: Path | None = None) -> None:
    """연 시각을 지금으로(목록 맨 위로)."""
    projects = load(path)
    for project in projects:
        if project.id == project_id:
            project.opened = datetime.datetime.now().replace(microsecond=0)
    save(projects, path)


def rename(project_id: str, name: str, path: Path | None = None) -> None:
    projects = load(path)
    for project in projects:
        if project.id == project_id and name.strip():
            project.name = name.strip()
    save(projects, path)


def remove(project_id: str, path: Path | None = None) -> None:
    """목록에서만 지운다(사진 폴더는 그대로)."""
    save([p for p in load(path) if p.id != project_id], path)


def _same(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return a == b


def _inside(path: Path | None, root: Path) -> bool:
    if path is None:
        return False
    try:
        path, root = path.resolve(), root.resolve()
    except OSError:
        return False
    return path == root or root in path.parents


def progress(project: Project, entries: list | None = None) -> list[StepState]:
    """작업 기록으로 단계마다 마지막으로 한 때를 찾는다."""
    entries = history.load() if entries is None else entries
    states = []
    for name, kinds, page in STEPS:
        latest = None
        for entry in entries:  # 최근 것부터
            if entry.kind in kinds and (_inside(entry.folder, project.root) or _inside(entry.source, project.root)):
                latest = entry
                break
        states.append(StepState(name, page, latest.time if latest else None, latest.summary if latest else ""))
    return states


def next_page(states: list[StepState]) -> str:
    """다음에 할 화면: 마지막으로 끝낸 단계의 다음. 아무것도 안 했으면 시간순 정렬(사진이 이미 폴더에
    있는 경우가 많아서), 다 했으면 베스트컷."""
    done = [i for i, s in enumerate(states) if s.done_at is not None]
    if not done:
        return "rename"
    last = max(done)
    return states[last + 1].page if last + 1 < len(states) else "selection"


def photo_folders(root: Path) -> list[Path]:
    """촬영 건 폴더 안의 사진 폴더(카드 가져오기의 날짜 폴더 등) — 바로 아래 폴더 중 사진이 있는 것,
    없으면 촬영 건 폴더 자체. 시간순 정렬처럼 폴더마다 다루는 화면에 넘긴다."""
    from card_import import MEDIA_EXTENSIONS

    root = Path(root)
    folders = []
    try:
        children = sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))
    except OSError:
        return [root]
    for child in children:
        try:
            if any(f.suffix.lower() in MEDIA_EXTENSIONS for f in child.iterdir() if f.is_file()):
                folders.append(child)
        except OSError:
            continue
    has_own = False
    try:
        has_own = any(f.suffix.lower() in MEDIA_EXTENSIONS for f in root.iterdir() if f.is_file())
    except OSError:
        pass
    if has_own or not folders:
        return [root, *folders] if has_own else [root]
    return folders
