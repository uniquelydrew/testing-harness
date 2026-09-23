"""Semantic default names for repository objects."""
from __future__ import annotations

import re
from typing import Iterable, Mapping

from automation_harness.core.component_catalog import display_name
from automation_harness.models.component import CapturedComponent
from automation_harness.models.gui import ObjectType, classify_accessibility


_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_SPACE = re.compile(r"\s+")


def default_component_name(capture: CapturedComponent) -> str:
    object_type = capture.semantic_type()
    type_name = display_name(object_type)
    text = _distinguishing_text(capture)
    if not text:
        return type_name
    text = _readable(text)
    if not text:
        return type_name
    if _already_contains_type(text, type_name):
        return text
    return "%s %s" % (text, type_name)


def unique_component_name(existing_names: Iterable[str], capture: CapturedComponent) -> str:
    existing = set(str(value) for value in existing_names)
    base = default_component_name(capture)
    if base not in existing:
        return base
    index = 2
    while "%s %d" % (base, index) in existing:
        index += 1
    return "%s %d" % (base, index)



def default_payload_name(payload: Mapping[str, object]) -> str:
    """Return the same user-facing default name for a serialized capture node."""
    raw_type = payload.get("object_type")
    try:
        object_type = ObjectType(str(raw_type)) if raw_type not in (None, "") else classify_accessibility(
            str(payload.get("role") or payload.get("accessible_role") or ""),
            str(payload.get("native_class") or payload.get("class") or ""),
        )
    except ValueError:
        object_type = classify_accessibility(
            str(payload.get("role") or payload.get("accessible_role") or ""),
            str(payload.get("native_class") or payload.get("class") or ""),
        )
    type_name = display_name(object_type)
    text = _payload_distinguishing_text(payload, object_type)
    if not text:
        return type_name
    text = _readable(text)
    if not text:
        return type_name
    if _already_contains_type(text, type_name):
        return text
    return "%s %s" % (text, type_name)


def unique_payload_name(
    existing_names: Iterable[str],
    payload: Mapping[str, object],
) -> str:
    existing = set(str(value) for value in existing_names)
    base = default_payload_name(payload)
    if base not in existing:
        return base
    index = 2
    while "%s %d" % (base, index) in existing:
        index += 1
    return "%s %d" % (base, index)

def _distinguishing_text(capture: CapturedComponent):
    properties = dict(capture.backend_properties or {})
    candidates = (
        properties.get("accessible_text"),
        properties.get("text"),
        properties.get("associated_label"),
        capture.name,
        capture.accessible_id,
    )
    native = str(capture.native_class or "")
    native_simple = native.rsplit(".", 1)[-1].casefold()
    role = str(capture.role or "").replace("_", " ").casefold()
    type_name = display_name(capture.semantic_type()).casefold()
    for value in candidates:
        if value in (None, ""):
            continue
        text = str(value).strip()
        folded = text.casefold()
        if folded in {native.casefold(), native_simple, role, type_name}:
            continue
        if "." in text and text.rsplit(".", 1)[-1].casefold() == native_simple:
            continue
        return text
    return None



def _payload_distinguishing_text(payload: Mapping[str, object], object_type: ObjectType):
    native = str(payload.get("native_class") or payload.get("class") or "")
    native_simple = native.rsplit(".", 1)[-1].casefold()
    role = str(payload.get("role") or payload.get("accessible_role") or "").replace("_", " ").casefold()
    type_name = display_name(object_type).casefold()
    candidates = (
        payload.get("accessible_text"),
        payload.get("text"),
        payload.get("name"),
        payload.get("associated_label"),
        payload.get("id"),
        payload.get("accessible_id"),
    )
    for value in candidates:
        if value in (None, ""):
            continue
        text = str(value).strip()
        folded = text.casefold()
        if folded in {native.casefold(), native_simple, role, type_name}:
            continue
        if "." in text and text.rsplit(".", 1)[-1].casefold() == native_simple:
            continue
        return text
    return None

def _readable(value: str) -> str:
    value = _CAMEL_BOUNDARY.sub(" ", str(value).strip())
    value = value.replace("_", " ").replace("-", " ")
    value = _SPACE.sub(" ", value).strip()
    return value[:1].upper() + value[1:] if value else value


def _already_contains_type(text: str, type_name: str) -> bool:
    folded = text.casefold()
    type_folded = type_name.casefold()
    if folded == type_folded or folded.endswith(" " + type_folded):
        return True
    # Common author-provided variants should not become "Username Field Text Field".
    if type_folded == "text field" and folded.endswith(" field"):
        return True
    return False
