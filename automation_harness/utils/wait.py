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
    stability_window: float = 0.0,
) -> T:
    if timeout <= 0 or interval <= 0 or stability_window < 0:
        raise ValueError("timeout and interval must be positive and stability_window cannot be negative")
    deadline = time.monotonic() + timeout
    last: T | None = None
    stable_since: float | None = None
    while True:
        last = supplier()
        if predicate(last):
            if stable_since is None:
                stable_since = time.monotonic()
            if time.monotonic() - stable_since >= stability_window:
                return last
        else:
            stable_since = None
        if time.monotonic() >= deadline:
            raise WaitTimeout(f"Timed out after {timeout:.3f}s waiting for {description}; last={last!r}")
        time.sleep(interval)
