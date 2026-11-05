"""작업 기록: 복사·이동·되돌리기·정렬·정리·축소·라이트룸·고객 셀렉 같은 파일 작업을 한 곳
(앱 데이터 폴더의 history.jsonl)에 한 줄씩 남겨 "파일 › 작업 기록"에서 날짜순으로 본다.

작업마다 자세한 기록(apply_log_*.jsonl 등)은 그 작업의 폴더에 따로 남기므로, 여기에는 언제·무엇을·
어디서 했는지와 그 기록 파일 경로만 둔다. 앱이 enable()을 부른 뒤에만 쓴다 — CLI·테스트는 쓰지 않는다.
쓰다가 실패해도(디스크 가득 등) 작업에는 영향이 없다.
"""

from __future__ import annotations

import datetime
import json
import threading
from dataclasses import dataclass
from pathlib import Path

HISTORY_NAME = "history.jsonl"
MAX_ENTRIES = 2000
_TRIM_BYTES = 1_000_000  # 이만큼 커지면 오래된 줄을 버린다

_path: Path | None = None
_lock = threading.Lock()


@dataclass(frozen=True)
class Entry:
    time: datetime.datetime
    kind: str  # "베스트컷 복사", "C컷 이동", "되돌리기", "시간순 정렬" …
    summary: str
    folder: Path | None
    log_file: Path | None
    source: Path | None = None  # 작업한 원본 사진 폴더(결과 폴더가 따로일 때 — 촬영 건 진행 상황용)


def enable(path: Path | None = None) -> Path:
    """기록을 켠다(앱 시작 때). path가 없으면 앱 데이터 폴더의 history.jsonl."""
    global _path
    if path is None:
        import app_log

        path = app_log.app_data_dir() / HISTORY_NAME
    _path = Path(path)
    return _path


def disable() -> None:
    global _path
    _path = None


def common_folder(paths) -> Path | None:
    """여러 파일이 담긴 폴더들의 공통 상위 폴더(없으면 None)."""
    import os

    folders = {str(Path(p).parent) for p in paths}
    if not folders:
        return None
    try:
        return Path(os.path.commonpath(sorted(folders)))
    except ValueError:  # 드라이브가 다름(Windows)
        return None


def record(kind: str, summary: str, folder: Path | None = None, log_file: Path | None = None,
           source: Path | None = None) -> None:
    if _path is None:
        return
    line = json.dumps(
        {
            "time": datetime.datetime.now().isoformat(timespec="seconds"),
            "kind": kind,
            "summary": summary,
            "folder": str(folder) if folder else None,
            "log_file": str(log_file) if log_file else None,
            "source": str(source) if source else None,
        },
        ensure_ascii=False,
    )
    try:
        with _lock:
            _path.parent.mkdir(parents=True, exist_ok=True)
            with open(_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            if _path.stat().st_size > _TRIM_BYTES:
                lines = _path.read_text(encoding="utf-8").splitlines()[-MAX_ENTRIES:]
                _path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass


def load(limit: int = MAX_ENTRIES) -> list[Entry]:
    """최근 것부터. 깨진 줄은 건너뛴다."""
    if _path is None or not _path.exists():
        return []
    entries = []
    try:
        lines = _path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in reversed(lines[-limit:]):
        try:
            data = json.loads(line)
            entries.append(
                Entry(
                    datetime.datetime.fromisoformat(data["time"]),
                    str(data["kind"]),
                    str(data["summary"]),
                    Path(data["folder"]) if data.get("folder") else None,
                    Path(data["log_file"]) if data.get("log_file") else None,
                    Path(data["source"]) if data.get("source") else None,
                )
            )
        except (ValueError, KeyError, TypeError):
            continue
    return entries


def path() -> Path | None:
    return _path
