"""Retry policy for transient object-resolution failures.

Desktop objects can legitimately appear a short time after the action that exposes
them. Resolution therefore retries the complete strategy chain until a bounded,
user-configurable deadline rather than failing after one scene/accessibility scan.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

DEFAULT_OBJECT_RESOLUTION_TIMEOUT = 5.0
DEFAULT_OBJECT_RESOLUTION_INTERVAL = 0.10
_TIMEOUT_ENV = "AUTOMATION_HARNESS_OBJECT_RESOLUTION_TIMEOUT"
_INTERVAL_ENV = "AUTOMATION_HARNESS_OBJECT_RESOLUTION_RETRY_INTERVAL"


def _preferences_path():
    root = os.environ.get("XDG_CONFIG_HOME")
    base = Path(root).expanduser() if root else Path.home() / ".config"
    return base / "automation-harness" / "preferences.json"


def _positive_float(value, fallback):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed >= 0.0 else fallback


def object_resolution_timeout(environ=None):
    """Return the configured resolution deadline in seconds.

    Environment configuration wins so CI/CLI runs can override a desktop user's
    preference. A timeout of zero intentionally disables retrying.
    """
    environ = os.environ if environ is None else environ
    if _TIMEOUT_ENV in environ:
        return _positive_float(environ.get(_TIMEOUT_ENV), DEFAULT_OBJECT_RESOLUTION_TIMEOUT)
    target = _preferences_path()
    if target.is_file():
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        if isinstance(raw, dict) and "object_resolution_timeout" in raw:
            return _positive_float(raw.get("object_resolution_timeout"), DEFAULT_OBJECT_RESOLUTION_TIMEOUT)
    return DEFAULT_OBJECT_RESOLUTION_TIMEOUT


def object_resolution_interval(environ=None):
    environ = os.environ if environ is None else environ
    return _positive_float(environ.get(_INTERVAL_ENV), DEFAULT_OBJECT_RESOLUTION_INTERVAL)


def install_component_handle_retry(component_handle_cls):
    """Install bounded retries around ComponentHandle resolution operations once."""
    if getattr(component_handle_cls, "_resolution_retry_installed", False):
        return component_handle_cls

    original_resolve = component_handle_cls.resolve
    original_state = component_handle_cls.state

    def retry(method_name, method, self):
        timeout = object_resolution_timeout()
        interval = object_resolution_interval()
        deadline = time.monotonic() + timeout
        attempts = 0
        last_error = None
        while True:
            attempts += 1
            try:
                result = method(self)
            except Exception as exc:
                last_error = exc
                now = time.monotonic()
                if now >= deadline:
                    try:
                        self.context.evidence.record(
                            "component_resolution_retry_exhausted",
                            component_id=self.definition.component_id,
                            operation=method_name,
                            attempts=attempts,
                            timeout=timeout,
                            error="%s: %s" % (type(exc).__name__, exc),
                        )
                    except Exception:
                        pass
                    raise
                try:
                    self.context.evidence.record(
                        "component_resolution_retry",
                        component_id=self.definition.component_id,
                        operation=method_name,
                        attempt=attempts,
                        timeout=timeout,
                        error="%s: %s" % (type(exc).__name__, exc),
                    )
                except Exception:
                    pass
                remaining = max(0.0, deadline - now)
                if remaining:
                    time.sleep(min(interval, remaining))
                continue
            if attempts > 1:
                try:
                    self.context.evidence.record(
                        "component_resolution_retry_succeeded",
                        component_id=self.definition.component_id,
                        operation=method_name,
                        attempts=attempts,
                        timeout=timeout,
                    )
                except Exception:
                    pass
            return result

    def resolve(self):
        return retry("resolve", original_resolve, self)

    def state(self):
        return retry("state", original_state, self)

    component_handle_cls.resolve = resolve
    component_handle_cls.state = state
    component_handle_cls._resolution_retry_installed = True
    component_handle_cls._resolution_retry_original_resolve = original_resolve
    component_handle_cls._resolution_retry_original_state = original_state
    return component_handle_cls
