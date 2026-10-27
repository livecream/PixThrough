"""사진 여러 장을 동시에 처리하는 작은 도우미(점수·유사도 지문·썸네일 계산).

사진 처리는 대부분 C 코드(JPEG 디코딩·OpenCV·mediapipe)에서 시간을 쓰고 그동안 GIL을
놓기 때문에, 프로세스 대신 스레드로 여러 장을 동시에 처리해도 코어 수만큼 빨라진다(패키징한
앱에서 프로세스를 띄우는 부담·메모리 중복도 없음). 결과 순서는 입력 순서와 같고, 진행률
콜백은 부른 쪽 스레드에서 한 장 끝날 때마다 불린다 — 콜백이 예외(취소 등)를 던지면 아직
시작하지 않은 일은 버리고, 돌고 있던 일이 끝나기를 기다린 뒤 그 예외를 그대로 올린다.
"""

from __future__ import annotations

import os
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Callable, Iterable, TypeVar

T = TypeVar("T")
R = TypeVar("R")

# 2400만 화소 사진 한 장을 채점하는 동안 수백 MB를 쓰므로(원본·흑백·라플라시안 배열)
# 코어가 많아도 동시에 이 이상은 돌리지 않는다.
MAX_WORKERS = 4


def default_workers() -> int:
    """코어 하나는 화면(GUI)에 남기고, 최대 MAX_WORKERS."""
    return max(1, min(MAX_WORKERS, (os.cpu_count() or 2) - 1))


def map_in_order(
    fn: Callable[[T], R],
    items: Iterable[T],
    workers: int | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[R]:
    """items마다 fn을 불러 결과를 입력 순서대로 돌려준다. workers가 1이면 지금 스레드에서
    차례로(예전과 똑같이) 처리한다."""
    items = list(items)
    total = len(items)
    workers = default_workers() if workers is None else max(1, workers)
    if workers == 1 or total <= 1:
        results = []
        for i, item in enumerate(items, start=1):
            results.append(fn(item))
            if on_progress is not None:
                on_progress(i, total)
        return results

    results: list[R] = [None] * total  # type: ignore[list-item]
    executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="pixthrough")
    try:
        pending = {executor.submit(fn, item): index for index, item in enumerate(items)}
        done_count = 0
        while pending:
            finished, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in finished:
                results[pending.pop(future)] = future.result()
                done_count += 1
                if on_progress is not None:
                    on_progress(done_count, total)
    finally:
        # 정상 종료면 이미 다 끝났고, 예외(취소)면 시작 전인 일은 버리고 돌던 일만 기다린다
        executor.shutdown(wait=True, cancel_futures=True)
    return results
