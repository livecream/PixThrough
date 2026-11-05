"""메모리카드 가져오기: 카드(또는 아무 폴더)의 사진·RAW·영상을 촬영 날짜별 폴더로 복사하고,
백업 드라이브가 정해져 있으면 같은 구조로 한 번 더 복사한다. 사용자 결정: 촬영 날짜별 폴더.

    <가져올 곳>/2026-11-03/IMG_0001.CR3
    <백업>/2026-11-03/IMG_0001.CR3

원본 보호:
- 카드의 파일은 읽기만 한다(지우거나 옮기지 않음). 카드를 지울지는 사용자가 정하고, 앱은 전부
  확인됐을 때 "지워도 안전" 여부만 알려 준다.
- 복사본은 `.part` 임시 이름으로 쓰면서 원본 해시를 재고, 다 쓴 뒤 복사본을 다시 읽어 해시가
  같을 때만 진짜 이름으로 바꾼다. 다르면 방금 만든 임시 파일만 지우고 실패로 남긴다.
- 대상에 이미 있는 파일은 덮어쓰지 않는다. 크기·수정 시각이 같고 해시도 같으면 "이미 가져옴"으로
  건너뛰고(다시 가져와도 그대로), 내용이 다른 같은 이름(바디 두 대의 IMG_0001, 카드 번호가 한 바퀴
  돈 경우)은 카드 안 폴더 이름을 붙여(`IMG_0001_101CANON.CR3`) JPG·RAW 짝이 같은 이름을 유지하게 한다.
- 처리 기록은 가져올 곳에 `import_log_<시각>.jsonl`.

촬영 시각은 EXIF → RAW(LibRaw, CR3 등) → 파일 수정 시각 순. 영상은 수정 시각.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import exifread

import app_log
import history
from raw_preview import RAW_EXTENSIONS, is_raw, raw_capture_time
from rename_by_time import VIDEO_EXTENSIONS
from scanner import SUPPORTED_EXTENSIONS

log = app_log.get("card_import")

MEDIA_EXTENSIONS = (
    {e.lower() for e in SUPPORTED_EXTENSIONS} | {e.lower() for e in RAW_EXTENSIONS} | {e.lower() for e in VIDEO_EXTENSIONS}
)
_CHUNK = 4 * 1024 * 1024

STATUS_COPY = "copy"  # 새로 복사
STATUS_RENAMED = "renamed"  # 같은 이름의 다른 파일이 있어 이름을 바꿔 복사
STATUS_EXISTS = "exists"  # 이미 가져옴(같은 파일) — 건너뜀
STATUS_LABELS = {
    STATUS_COPY: "복사",
    STATUS_RENAMED: "이름 바꿔 복사",
    STATUS_EXISTS: "이미 가져옴",
}


@dataclass(frozen=True)
class ImportRow:
    source: Path
    relative: Path  # 가져올 곳·백업 아래 경로(날짜 폴더/이름)
    taken: datetime.datetime
    size: int
    status: str  # 가져올 곳 기준
    backup_status: str | None = None  # 백업 기준(백업을 안 하면 None)


@dataclass
class ImportPlan:
    source: Path
    destination: Path
    backup: Path | None
    rows: list[ImportRow]
    skipped_other: int = 0  # 사진·RAW·영상이 아닌 파일(가져오지 않음)

    @property
    def to_copy(self) -> list[ImportRow]:
        """새로 쓸 것이 있는 파일(가져올 곳이나 백업 중 한 곳이라도)."""
        return [r for r in self.rows if r.status != STATUS_EXISTS or (self.backup and r.backup_status != STATUS_EXISTS)]

    @property
    def total_bytes(self) -> int:
        """새로 써야 할 용량(가져올 곳 + 백업)."""
        total = 0
        for row in self.rows:
            if row.status != STATUS_EXISTS:
                total += row.size
            if self.backup is not None and row.backup_status != STATUS_EXISTS:
                total += row.size
        return total


@dataclass
class ImportResult:
    row: ImportRow
    destination: Path | None = None
    backup: Path | None = None
    copied: bool = False  # 가져올 곳에 새로 썼음
    backed_up: bool = False  # 백업에 새로 썼음
    error: str | None = None


@dataclass
class ImportOutcome:
    results: list[ImportResult]
    log_file: Path | None
    total: int = 0  # 계획의 파일 수(멈췄으면 results가 더 적음)
    errors: list[ImportResult] = field(default_factory=list)

    @property
    def safe_to_erase(self) -> bool:
        """모든 파일이 가져올 곳(그리고 백업)에 해시까지 같게 있는가 — 카드를 지워도 되는지."""
        return bool(self.results) and len(self.results) == self.total and not self.errors


def _guard(source: Path, destination: Path, backup: Path | None) -> None:
    def overlap(a: Path, b: Path) -> bool:
        a, b = a.resolve(), b.resolve()
        return a == b or a in b.parents or b in a.parents

    if overlap(source, destination):
        raise ValueError("가져올 곳이 카드(원본) 폴더와 같거나 서로 안에 들어 있습니다. 겹치지 않는 폴더를 고르세요.")
    if backup is not None:
        if overlap(source, backup):
            raise ValueError("백업 폴더가 카드(원본) 폴더와 같거나 서로 안에 들어 있습니다.")
        if overlap(destination, backup):
            raise ValueError("백업 폴더가 가져올 곳과 같거나 서로 안에 들어 있습니다. 다른 드라이브를 고르세요.")


def capture_time(path: Path) -> datetime.datetime:
    path = Path(path)
    if path.suffix.lower() not in VIDEO_EXTENSIONS:
        try:
            with open(path, "rb") as f:
                tags = exifread.process_file(f, details=False, stop_tag="DateTimeOriginal")
            raw = tags.get("EXIF DateTimeOriginal") or tags.get("Image DateTime")
            if raw is not None:
                return datetime.datetime.strptime(str(raw), "%Y:%m:%d %H:%M:%S")
        except Exception:  # noqa: BLE001 - EXIF가 없거나 깨짐 → 다음 방법
            pass
        if is_raw(path):
            taken = raw_capture_time(path)
            if taken is not None:
                return taken
    return datetime.datetime.fromtimestamp(path.stat().st_mtime)


def _media_files(source: Path) -> tuple[list[Path], int]:
    files, other = [], 0
    for root, dirs, names in os.walk(source):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))  # .Trashes·.Spotlight 등
        for name in sorted(names):
            if name.startswith("."):  # ._IMG(맥 리소스 포크)·.DS_Store
                continue
            path = Path(root) / name
            if path.suffix.lower() in MEDIA_EXTENSIONS:
                files.append(path)
            else:
                other += 1
    return files, other


def _same_file(a: Path, size: int, mtime: float) -> bool:
    """크기·수정 시각이 같으면 같은 파일로 본다(복사할 때 수정 시각을 그대로 옮김). 실제 복사 때 해시로 다시 확인."""
    try:
        stat = a.stat()
    except OSError:
        return False
    return stat.st_size == size and abs(stat.st_mtime - mtime) < 2


def _status_at(root: Path | None, relative: Path, size: int, mtime: float, taken_names: set[Path]) -> str | None:
    if root is None:
        return None
    target = root / relative
    if target.exists():
        return STATUS_EXISTS if _same_file(target, size, mtime) else STATUS_RENAMED
    if Path(str(relative).lower()) in taken_names:
        return STATUS_RENAMED
    return STATUS_COPY


def _renamed(relative: Path, source: Path, counter: int = 0) -> Path:
    tag = source.parent.name or "card"
    suffix = f"_{tag}" + (f"_{counter}" if counter else "")
    return relative.with_name(f"{relative.stem}{suffix}{relative.suffix}")


def build_import_plan(
    source: Path,
    destination: Path,
    backup: Path | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> ImportPlan:
    """카드에서 무엇을 어디로 복사할지(아직 아무것도 쓰지 않음)."""
    source, destination = Path(source), Path(destination)
    backup = Path(backup) if backup else None
    _guard(source, destination, backup)
    files, other = _media_files(source)
    rows: list[ImportRow] = []
    used: set[Path] = set()  # 이번 계획에서 이미 쓰기로 한 경로(대소문자 무시)
    for index, path in enumerate(files, start=1):
        stat = path.stat()
        taken = capture_time(path)
        relative = Path(taken.strftime("%Y-%m-%d")) / path.name
        status = _status_at(destination, relative, stat.st_size, stat.st_mtime, used)
        backup_status = _status_at(backup, relative, stat.st_size, stat.st_mtime, used)
        if STATUS_RENAMED in (status, backup_status):
            counter = 0
            candidate = _renamed(relative, path)
            while True:
                status = _status_at(destination, candidate, stat.st_size, stat.st_mtime, used)
                backup_status = _status_at(backup, candidate, stat.st_size, stat.st_mtime, used)
                if STATUS_RENAMED not in (status, backup_status):
                    break
                counter += 1
                candidate = _renamed(relative, path, counter)
            relative = candidate
            if status == STATUS_COPY:
                status = STATUS_RENAMED
        used.add(Path(str(relative).lower()))
        rows.append(ImportRow(path, relative, taken, stat.st_size, status, backup_status))
        if on_progress is not None:
            on_progress(index, len(files))
    rows.sort(key=lambda r: (r.taken, str(r.relative)))
    return ImportPlan(source, destination, backup, rows, other)


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_verified(source: Path, target: Path, source_hash: str | None) -> tuple[bool, str]:
    """source를 target에 확인하며 복사한다. 돌려주는 값: (새로 썼는지, 원본 해시).
    target에 이미 같은 파일이 있으면 쓰지 않는다. 내용이 다른 파일이 있으면 FileExistsError."""
    if target.exists():
        source_hash = source_hash or _hash(source)
        if _hash(target) == source_hash:
            return False, source_hash
        raise FileExistsError(f"같은 이름의 다른 파일이 이미 있습니다: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    digest = hashlib.sha256()
    try:
        with open(source, "rb") as src, open(part, "xb") as dst:
            for chunk in iter(lambda: src.read(_CHUNK), b""):
                digest.update(chunk)
                dst.write(chunk)
            dst.flush()
            os.fsync(dst.fileno())
        written = digest.hexdigest()
        if source_hash is not None and written != source_hash:
            raise OSError(f"카드에서 읽을 때마다 내용이 다릅니다(카드 오류일 수 있음): {source.name}")
        if _hash(part) != written:
            raise OSError(f"복사본이 원본과 다릅니다(디스크 오류일 수 있음): {target}")
        shutil.copystat(source, part)
        if target.exists():  # 그사이 누가 같은 이름을 만들었으면 덮어쓰지 않는다
            raise FileExistsError(f"같은 이름의 파일이 생겼습니다: {target}")
        os.replace(part, target)
        return True, written
    finally:
        if part.exists():
            part.unlink()  # 방금 만든 임시 파일만 지운다


def apply_import(plan: ImportPlan, on_progress: Callable[[int, int], None] | None = None) -> ImportOutcome:
    """계획대로 복사한다. 한 파일이 실패해도 나머지는 계속한다(취소는 on_progress가 예외로)."""
    results: list[ImportResult] = []
    total = len(plan.rows)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    plan.destination.mkdir(parents=True, exist_ok=True)
    log_file = plan.destination / f"import_log_{stamp}.jsonl"
    try:
        with open(log_file, "w", encoding="utf-8") as log_out:
            for index, row in enumerate(plan.rows, start=1):
                result = ImportResult(row, plan.destination / row.relative,
                                      plan.backup / row.relative if plan.backup else None)
                try:
                    result.copied, source_hash = _copy_verified(row.source, result.destination, None)
                    if result.backup is not None:
                        result.backed_up, _ = _copy_verified(row.source, result.backup, source_hash)
                except OSError as e:
                    result.error = str(e)
                    log.warning("가져오기 실패 %s: %s", row.source, e)
                results.append(result)
                log_out.write(json.dumps({
                    "source": str(row.source),
                    "destination": str(result.destination),
                    "backup": str(result.backup) if result.backup else None,
                    "taken": row.taken.isoformat(timespec="seconds"),
                    "copied": result.copied,
                    "backed_up": result.backed_up,
                    "error": result.error,
                }, ensure_ascii=False) + "\n")
                log_out.flush()
                if on_progress is not None:
                    on_progress(index, total)
    finally:
        copied = sum(1 for r in results if r.copied)
        errors = [r for r in results if r.error]
        summary = f"{copied}개 복사 · 이미 있음 {sum(1 for r in results if not r.copied and not r.error)}개"
        if plan.backup is not None:
            summary += f" · 백업 {sum(1 for r in results if r.backed_up)}개"
        if errors:
            summary += f" · 실패 {len(errors)}개"
        if len(results) < total:
            summary += f" · 멈춤({len(results)}/{total})"
        history.record("카드 가져오기", summary, plan.destination, log_file)
        log.info("카드 가져오기 %s → %s: %s", plan.source, plan.destination, summary)
    return ImportOutcome(results, log_file, total, errors)


def find_cards() -> list[Path]:
    """연결된 메모리카드(맨 위에 DCIM 폴더가 있는 드라이브)를 찾는다 — macOS /Volumes,
    Windows 드라이브 문자, 리눅스 /media·/run/media."""
    candidates: list[Path] = []
    if os.name == "nt":
        import string

        candidates = [Path(f"{letter}:/") for letter in string.ascii_uppercase[3:]]
    else:
        for base in (Path("/Volumes"), Path("/media"), Path("/run/media")):
            try:
                for child in base.iterdir():
                    candidates.append(child)
                    if base != Path("/Volumes"):  # /media/<사용자>/<카드>
                        candidates.extend(c for c in child.iterdir() if c.is_dir())
            except OSError:
                continue
    cards = []
    for path in candidates:
        try:
            if (path / "DCIM").is_dir():
                cards.append(path)
        except OSError:
            continue
    return cards
