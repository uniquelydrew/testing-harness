from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


class WaitTimeout(TimeoutError):
    pass


def wait_for(
    supplier: Callable[[], T],
    predicate: Callable[[T], bool] = bool,
    *,
    timeout: float = 5.0,
    interval: float = 0.05,
    description: str = "condition",
) -> T:
    deadline = time.monotonic() + timeout
    last: T | None = None
    while True:
        last = supplier()
        if predicate(last):
            return last
        if time.monotonic() >= deadline:
            raise WaitTimeout(f"Timed out after {timeout:.3f}s waiting for {description}; last={last!r}")
        time.sleep(interval)
