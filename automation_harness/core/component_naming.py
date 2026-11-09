"""Semantic default names for repository objects."""
from __future__ import annotations

import re
from typing import Iterable, Mapping

from automation_harness.core.component_catalog import display_name
from automation_harness.models.component import CapturedComponent
from automation_harness.models.gui import ObjectType, classify_accessibility


_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_SPACE = re.compile(r"\s+")
_ACRONYM_BOUNDARY = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")
_GENERIC_NAMES = frozenset({"object", "component", "unknown", "unnamed", "n/a"})
_GENERIC_NATIVE_PACKAGES = (
    "java.", "javax.", "javafx.", "com.sun.javafx.", "com.jogamp.",
)


def _native_class_name(native_class: str | None) -> str | None:
    """Best-effort display fallback, never a locator or inferred semantic role.

    Application-specific class names frequently convey more useful intent than
    an accessible name such as 'Object'. Framework classes do not.
    """
    full_name = str(native_class or "").strip()
    if not full_name or full_name.startswith(_GENERIC_NATIVE_PACKAGES):
        return None
    simple = full_name.rsplit(".", 1)[-1].split("$", 1)[0]
    if not simple or simple.startswith(("Anonymous", "lambda")):
        return None
    words = _ACRONYM_BOUNDARY.sub(" ", _CAMEL_BOUNDARY.sub(" ", simple)).split()
    if not words:
        return None

    # A short implementation prefix can be removed only when the package
    # corroborates the descriptive stem (e.g. trackstate/UiTrackState...).
    package_parts = full_name.rsplit(".", 1)[0].casefold().split(".")
    if (
        len(words) >= 4 and 2 <= len(words[0]) <= 4
        and len(words[1]) >= 3
        and "".join(words[1:3]).casefold() in package_parts
    ):
        words = words[1:]
    readable = _readable(" ".join(words))
    return readable if any(len(word) > 2 for word in words) else None



def default_component_name(capture: CapturedComponent) -> str:
    object_type = capture.semantic_type()
    type_name = _display_name_for_native(object_type, capture.native_class)
    text = _distinguishing_text(capture)
    if not text:
        return _native_class_name(capture.native_class) or type_name
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
    type_name = _display_name_for_native(
        object_type,
        str(payload.get("native_class") or payload.get("class") or ""),
    )
    text = _payload_distinguishing_text(payload, object_type)
    if not text:
        return _native_class_name(
            str(payload.get("native_class") or payload.get("class") or "")
        ) or type_name
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
    type_name = _display_name_for_native(
        capture.semantic_type(), capture.native_class,
    ).casefold()
    for value in candidates:
        if value in (None, ""):
            continue
        text = str(value).strip()
        folded = text.casefold()
        if folded in {native.casefold(), native_simple, role, type_name} | _GENERIC_NAMES:
            continue
        if "." in text and text.rsplit(".", 1)[-1].casefold() == native_simple:
            continue
        return text
    return None



def _payload_distinguishing_text(payload: Mapping[str, object], object_type: ObjectType):
    native = str(payload.get("native_class") or payload.get("class") or "")
    native_simple = native.rsplit(".", 1)[-1].casefold()
    role = str(payload.get("role") or payload.get("accessible_role") or "").replace("_", " ").casefold()
    type_name = _display_name_for_native(object_type, native).casefold()
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
        if folded in {native.casefold(), native_simple, role, type_name} | _GENERIC_NAMES:
            continue
        if "." in text and text.rsplit(".", 1)[-1].casefold() == native_simple:
            continue
        return text
    return None


def _display_name_for_native(object_type: ObjectType, native_class: str | None) -> str:
    """Use concrete JavaFX selector labels without changing semantic type."""
    simple = str(native_class or "").rsplit(".", 1)[-1].casefold()
    if object_type is ObjectType.COMBO_BOX:
        if simple == "choicebox":
            return "Choice Box"
        if simple == "combobox":
            return "Combo Box"
    return display_name(object_type)

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
