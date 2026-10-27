import threading
import time

import pytest

from parallel import map_in_order


@pytest.mark.parametrize("workers", [1, 3])
def test_results_keep_input_order_and_progress_counts_up(workers):
    progress = []

    def slow_square(n):
        time.sleep(0.01 * (5 - n))  # 늦게 시작한 것이 먼저 끝나도
        return n * n

    assert map_in_order(slow_square, range(5), workers, lambda d, t: progress.append((d, t))) == [0, 1, 4, 9, 16]
    assert progress == [(i, 5) for i in range(1, 6)]


def test_runs_on_several_threads():
    seen = set()

    def record(_):
        seen.add(threading.get_ident())
        time.sleep(0.05)

    map_in_order(record, range(6), workers=3)

    assert len(seen) > 1


def test_exception_from_progress_stops_remaining_work():
    started = []

    class Cancelled(Exception):
        pass

    def work(n):
        started.append(n)
        time.sleep(0.02)
        return n

    def cancel_after_first(done, total):
        raise Cancelled

    with pytest.raises(Cancelled):
        map_in_order(work, range(50), workers=2, on_progress=cancel_after_first)
    assert len(started) < 50


def test_error_in_work_is_raised():
    def boom(n):
        raise ValueError(n)

    with pytest.raises(ValueError):
        map_in_order(boom, [1, 2], workers=2)
