"""Runtime policy for durable JavaFX resolution of transient skin nodes.

JavaFX ContextMenu/Menu skins are rebuilt when their popup is shown. Recording
can therefore observe a private ``com.sun.javafx`` MenuItemContainer that no
longer exists by the time playback resolves the persisted object. Durable
identity must prefer authored semantic properties (especially Node.id) and use
skin/hierarchy evidence only as a capture-time diagnostic.
"""
from __future__ import annotations

import time
from collections import OrderedDict
from typing import Mapping

from automation_harness.core.resolution_retry import (
    object_resolution_interval,
    object_resolution_timeout,
)


_TRANSIENT_WINDOW_NAMES = {"contextmenu", "popupwindow", "popup"}
_UNSTABLE_ASSISTIVE_KEYS = {"hierarchy", "style_classes"}


def _is_internal_class(value):
    text = str(value or "")
    return text.startswith("com.sun.javafx.") or text.startswith("com.sun.glass.")


def _sanitize_descriptor(value):
    if not isinstance(value, Mapping):
        return value
    result = dict(value)
    if _is_internal_class(result.get("class")):
        result.pop("class", None)
    return result


def _sanitized_identity(identification):
    raw = dict(identification or {})
    mandatory = raw.get("mandatory")
    assistive = raw.get("assistive")
    if not isinstance(mandatory, Mapping) or not isinstance(assistive, Mapping):
        return raw

    cleaned = OrderedDict()
    for key, value in assistive.items():
        if key in _UNSTABLE_ASSISTIVE_KEYS:
            continue
        if key == "class" and _is_internal_class(value):
            continue
        if key == "parent":
            value = _sanitize_descriptor(value)
            if isinstance(value, Mapping) and not value:
                continue
        elif key == "lineage" and isinstance(value, (list, tuple)):
            value = [item for item in (_sanitize_descriptor(item) for item in value) if item]
            if not value:
                continue
        cleaned[key] = value

    result = {"mandatory": dict(mandatory)}
    if cleaned:
        result["assistive"] = dict(cleaned)
    if "ordinal" in raw:
        result["ordinal"] = raw["ordinal"]
    return result


def _mandatory_identity(identification):
    raw = dict(identification or {})
    mandatory = raw.get("mandatory")
    if not isinstance(mandatory, Mapping) or not mandatory:
        return None
    result = {"mandatory": dict(mandatory)}
    if "ordinal" in raw:
        result["ordinal"] = raw["ordinal"]
    return result


def resolution_candidates(identification):
    """Return increasingly durable interpretations of a stored JavaFX locator."""
    original = dict(identification or {})
    candidates = [original]
    sanitized = _sanitized_identity(original)
    if sanitized != original:
        candidates.append(sanitized)
    mandatory = _mandatory_identity(original)
    if mandatory is not None and mandatory not in candidates:
        candidates.append(mandatory)
    return tuple(candidates)


def install_javafx_transient_resolution(driver_cls=None):
    """Install compatibility fallback and bounded retries on JavaFxBridgeDriver.

    The patch is intentionally runtime-side so repositories authored before the
    durable-identity correction do not need to be re-recorded. New captures
    should still avoid persisting private JavaFX skin evidence.
    """
    if driver_cls is None:
        from automation_harness.drivers.javafx_bridge import JavaFxBridgeDriver
        driver_cls = JavaFxBridgeDriver
    if getattr(driver_cls, "_transient_resolution_policy_installed", False):
        return driver_cls

    original_find_matches = driver_cls._find_matches
    original_find_unique = driver_cls._find_unique

    def find_matches(self, identification, *, process_id=None):
        last = ([], ())
        for candidate in resolution_candidates(identification):
            matches, trace = original_find_matches(self, candidate, process_id=process_id)
            last = (matches, trace)
            if matches:
                return matches, trace
        return last

    # Install candidate relaxation before wrapping _find_unique so every retry
    # gets a fresh scene scan using the durable fallback sequence.
    driver_cls._find_matches = find_matches

    def find_unique(self, identification, *, process_id=None):
        timeout = object_resolution_timeout()
        interval = object_resolution_interval()
        deadline = time.monotonic() + timeout
        attempts = 0
        while True:
            attempts += 1
            try:
                # original_find_unique dispatches through self._find_matches,
                # which is now the compatibility-aware implementation above.
                return original_find_unique(self, identification, process_id=process_id)
            except LookupError as exc:
                # Ambiguity is a locator defect, not an appearance delay.
                if "ambiguous" in str(exc).casefold():
                    raise
                now = time.monotonic()
                if now >= deadline:
                    raise
                remaining = max(0.0, deadline - now)
                if remaining:
                    time.sleep(min(interval, remaining))

    driver_cls._find_unique = find_unique
    driver_cls._transient_resolution_policy_installed = True
    driver_cls._transient_resolution_original_find_matches = original_find_matches
    driver_cls._transient_resolution_original_find_unique = original_find_unique
    return driver_cls
