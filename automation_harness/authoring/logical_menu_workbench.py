"""Workbench projection for logical JavaFX menu captures.

Recording captures Menu/MenuItem semantics independently of disposable skin
nodes. This adapter projects those logical owner/path descriptors into the
Object Identity Workbench so menu descendants appear under their owning menu
rather than as unrelated top-level objects.
"""
from __future__ import annotations

from typing import Mapping


_INSTALLED = False
_ORIGINAL = None


def install() -> None:
    global _INSTALLED, _ORIGINAL
    if _INSTALLED:
        return
    from automation_harness.authoring import capture_context

    _ORIGINAL = capture_context.build_recording_context
    capture_context.build_recording_context = build_recording_context
    _INSTALLED = True


def build_recording_context(captures):
    from automation_harness.authoring import capture_context

    captures = tuple(item for item in captures if item is not None)
    if not captures or not any(_logical_menu_metadata(item) for item in captures):
        return _ORIGINAL(captures)

    distinct = []
    seen = set()
    for captured in captures:
        identity = capture_context._recorded_capture_identity(captured)
        if identity in seen:
            continue
        seen.add(identity)
        distinct.append(captured)
    if not distinct:
        raise ValueError("recording contains no semantic targets")

    root = capture_context.CaptureContextNode(
        key="recording-root",
        label="Recorded interaction scope",
        payload={},
        is_window_root=True,
        is_semantic=False,
    )
    windows = {}
    captured_by_key = {}
    target_keys = []

    for index, captured in enumerate(distinct):
        window_name = str(
            getattr(captured, "window", None)
            or getattr(captured, "application", None)
            or "Application Window"
        )
        window = windows.get(window_name)
        if window is None:
            window = capture_context.CaptureContextNode(
                key="recording-window-%d" % len(windows),
                label=window_name,
                payload={"window": window_name},
                is_window_root=True,
                is_semantic=False,
            )
            windows[window_name] = window
            root.children.append(window)

        metadata = _logical_menu_metadata(captured)
        if metadata:
            parent = _menu_parent(window, metadata, index, capture_context)
            path = metadata.get("path")
            path = path if isinstance(path, (list, tuple)) else ()
            for segment_index, raw in enumerate(path[:-1]):
                if not isinstance(raw, Mapping):
                    continue
                parent = _path_node(parent, raw, index, segment_index, capture_context)
        else:
            parent = window

        target_key = "recording-target-%d" % index
        payload = capture_context._recorded_capture_payload(captured)
        target = capture_context.CaptureContextNode(
            key=target_key,
            label=_target_label(captured, metadata, capture_context, payload),
            payload=payload,
            is_target=True,
            is_semantic=True,
        )
        parent.children.append(target)
        captured_by_key[target_key] = captured
        target_keys.append(target_key)

    frameworks = {str(item.framework or "") for item in distinct}
    return capture_context.CaptureContext(
        framework="mixed" if len(frameworks) > 1 else str(distinct[0].framework or "desktop"),
        root=root,
        target_key=target_keys[0],
        target_keys=tuple(target_keys),
        captured_by_key=captured_by_key,
    )


def _logical_menu_metadata(captured):
    properties = dict(getattr(captured, "backend_properties", {}) or {})
    value = properties.get("logical_menu")
    return value if isinstance(value, Mapping) else None


def _menu_parent(window, metadata, capture_index, capture_context):
    owner = metadata.get("owner")
    owner = owner if isinstance(owner, Mapping) else {}
    label = _owner_label(owner)
    key = "logical-menu-owner:%s" % _key_fragment(label)
    existing = next((child for child in window.children if child.key == key), None)
    if existing is not None:
        return existing
    node = capture_context.CaptureContextNode(
        key=key,
        label=label,
        payload={"logical_menu_owner": dict(owner)},
        is_target=False,
        is_semantic=False,
    )
    window.children.append(node)
    return node


def _path_node(parent, raw, capture_index, segment_index, capture_context):
    label = _selector_label(raw)
    criteria = raw.get("criteria")
    criteria = dict(criteria) if isinstance(criteria, Mapping) else {}
    key = "%s/menu:%s" % (parent.key, _key_fragment(str(criteria.get("id") or label)))
    existing = next((child for child in parent.children if child.key == key), None)
    if existing is not None:
        return existing
    node = capture_context.CaptureContextNode(
        key=key,
        label=label,
        payload={"logical_menu_selector": dict(raw)},
        is_target=False,
        is_semantic=False,
    )
    parent.children.append(node)
    return node


def _target_label(captured, metadata, capture_context, payload):
    if metadata:
        path = metadata.get("path")
        if isinstance(path, (list, tuple)) and path and isinstance(path[-1], Mapping):
            return _selector_label(path[-1])
    return capture_context.node_label(payload)


def _owner_label(owner):
    for field in ("logical", "node", "popup"):
        value = owner.get(field)
        if not isinstance(value, Mapping):
            continue
        if field == "logical":
            return _selector_label(value)
        name = value.get("name") or value.get("text") or value.get("accessible_id") or value.get("id")
        if name:
            return str(name)
    kind = str(owner.get("kind") or "Menu").replace("_", " ")
    return kind.title()


def _selector_label(selector):
    criteria = selector.get("criteria")
    criteria = criteria if isinstance(criteria, Mapping) else {}
    return str(criteria.get("text") or criteria.get("id") or selector.get("kind") or "Menu Item")


def _key_fragment(value):
    normalized = "".join(character.lower() if character.isalnum() else "-" for character in value).strip("-")
    return normalized or "menu"
