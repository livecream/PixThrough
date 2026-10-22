"""사진 점수·유사도 지문을 앱을 꺼도 남기는 저장소.

스코어링은 사진 1장당 ~100ms대(얼굴 검출 포함)라 수천 장이면 분 단위가 걸린다. 같은
사진을 다음에 다시 열 때 처음부터 다시 계산하지 않도록, 세션 캐시(경로 →
(수정 시각, 크기, 값))를 앱 데이터 폴더(모델 파일 옆 cache/)에 JSON으로 저장해 둔다.

- 파일의 수정 시각이나 크기가 바뀌면 기존 캐시와 똑같이 그 사진만 다시 계산된다.
- 점수 계산 방식이 바뀌면(scoring.SCORING_VERSION / image_hash.HASH_VERSION을 올리면)
  저장된 파일 전체를 무시하고 처음부터 다시 계산한다.
- 점수 계산에 실패한 사진은 저장하지 않는다(다음에 다시 시도).
- 연결이 끊긴 외장 드라이브의 사진도 다시 꽂았을 때 쓰도록, 없는 파일이라고 지우지
  않는다. 대신 MAX_ENTRIES를 넘으면 오래 전에 넣은 것부터 버린다.

베스트컷·C컷 탭이 같은 딕셔너리를 쓰므로 한 탭에서 채점한 사진은 다른 탭에서 바로 뜬다.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, fields
from pathlib import Path

import scoring
from image_hash import HASH_VERSION
from scoring import SCORING_VERSION, PhotoScore

MAX_ENTRIES = 50_000

_score_cache: dict | None = None
_hash_cache: dict | None = None


def cache_dir() -> Path:
    return scoring._MODEL_DIR.parent / "cache"


def _scores_path() -> Path:
    return cache_dir() / "scores.json"


def _hashes_path() -> Path:
    return cache_dir() / "hashes.json"


def _read(path: Path, version: int) -> dict:
    """저장 파일의 entries(경로 문자열 → [mtime, size, 값])를 읽는다. 없거나 깨졌거나
    버전이 다르면 빈 딕셔너리."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("version") != version:
        return {}
    entries = data.get("entries")
    return entries if isinstance(entries, dict) else {}


def _write(path: Path, version: int, entries: dict) -> None:
    """다른 곳에서 읽는 도중 반쯤 쓴 파일이 보이지 않도록 임시 파일에 쓰고 바꿔치기한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": version, "entries": entries}), encoding="utf-8")
    os.replace(tmp, path)


def _decode_score(raw: dict) -> PhotoScore:
    names = {f.name for f in fields(PhotoScore)}
    values = {k: v for k, v in raw.items() if k in names}
    values["path"] = Path(values["path"])
    return PhotoScore(**values)


def score_cache() -> dict[Path, tuple[float, int, PhotoScore]]:
    """앱 전체가 함께 쓰는 점수 캐시. 처음 부를 때 디스크에서 읽어온다."""
    global _score_cache
    if _score_cache is None:
        _score_cache = {}
        for key, entry in _read(_scores_path(), SCORING_VERSION).items():
            try:
                mtime, size, raw = entry
                _score_cache[Path(key)] = (mtime, size, _decode_score(raw))
            except (TypeError, ValueError, KeyError):
                continue
    return _score_cache


def hash_cache() -> dict[Path, tuple[float, int, int | None]]:
    """앱 전체가 함께 쓰는 유사도 지문 캐시. 처음 부를 때 디스크에서 읽어온다."""
    global _hash_cache
    if _hash_cache is None:
        _hash_cache = {}
        for key, entry in _read(_hashes_path(), HASH_VERSION).items():
            try:
                mtime, size, value = entry
                _hash_cache[Path(key)] = (mtime, size, value)
            except (TypeError, ValueError):
                continue
    return _hash_cache


def _trimmed(cache: dict) -> list:
    items = list(cache.items())
    return items[-MAX_ENTRIES:]


def save() -> None:
    """지금까지 메모리에 쌓인 점수·지문을 디스크에 쓴다. 저장에 실패해도(디스크 가득 등)
    앱 동작에는 영향이 없도록 조용히 넘어간다 — 다음에 다시 계산될 뿐이다."""
    try:
        if _score_cache is not None:
            entries = {
                str(path): [mtime, size, {**asdict(score), "path": str(score.path)}]
                for path, (mtime, size, score) in _trimmed(_score_cache)
                if score.error is None
            }
            _write(_scores_path(), SCORING_VERSION, entries)
        if _hash_cache is not None:
            entries = {
                str(path): [mtime, size, value]
                for path, (mtime, size, value) in _trimmed(_hash_cache)
                if value is not None
            }
            _write(_hashes_path(), HASH_VERSION, entries)
    except OSError:
        pass


def clear() -> None:
    """저장된 점수·지문을 메모리와 디스크에서 모두 지운다(같은 딕셔너리 객체를 비워,
    이미 들고 있는 탭들도 함께 비워진다)."""
    for cache in (_score_cache, _hash_cache):
        if cache is not None:
            cache.clear()
    for path in (_scores_path(), _hashes_path()):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
