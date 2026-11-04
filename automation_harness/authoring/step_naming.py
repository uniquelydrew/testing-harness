"""Deterministic, human-readable default names for authored and recorded steps.

Step names are presentation metadata, never component identity or locator data.
An explicitly authored name is authoritative; only missing names are synthesized.
"""
from __future__ import annotations

import re
from uuid import UUID

from automation_harness.models.gui import ObjectType


_ACRONYMS = {
    "api": "API", "atspi": "AT-SPI", "css": "CSS", "gui": "GUI",
    "html": "HTML", "http": "HTTP", "https": "HTTPS", "id": "ID",
    "io": "IO", "ip": "IP", "jvm": "JVM", "json": "JSON",
    "oidc": "OIDC", "sql": "SQL", "ssl": "SSL",
    "tcp": "TCP", "tts": "TTS", "ui": "UI", "url": "URL",
    "uuid": "UUID", "vm": "VM", "xml": "XML", "x11": "X11",
}
_ALIASES = {"btn": "Button", "dlg": "Dialog", "txt": "Text"}
_ACTIONS = {
    "select_menu_item": "Select Menu Option",
    "gui.object.visual.assert": "Assert Match",
    "gui.object.exists.assert": "Assert Exists",
    "gui.object.state.assert": "Assert State",
    "component.state.wait": "Wait for State",
    "gui.object.property.get": "Read Property",
}
_KIND_SUFFIX = {
    ObjectType.BUTTON: "Button",
    ObjectType.TOGGLE_BUTTON: "Toggle Button",
    ObjectType.CHECK_BOX: "Check Box",
    ObjectType.RADIO_BUTTON: "Radio Button",
    ObjectType.TEXT_FIELD: "Text Field",
    ObjectType.PASSWORD_FIELD: "Password Field",
    ObjectType.TEXT_AREA: "Text Area",
    ObjectType.COMBO_BOX: "Combo Box",
    ObjectType.WINDOW: "Window",
    ObjectType.DIALOG: "Dialog",
    ObjectType.TAB: "Tab",
    ObjectType.MENU: "Menu",
    ObjectType.MENU_ITEM: "Menu Item",
}


def _is_uuid(value):
    try:
        UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def humanize_identifier(value, *, leaf=False):
    """Split snake_case, kebab-case and CamelCase without damaging acronyms.

    Repository names are qualified by their owner, so prefer the final logical
    name for an individual object while keeping its canonical UUID elsewhere.
    """
    text = str(value or "").strip()
    if leaf:
        text = text.rsplit(".", 1)[-1].rsplit("/", 1)[-1].strip()
    if not text or _is_uuid(text):
        return ""
    # Preserve acronym words such as HTTPServer, but break at mixed-case edges.
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    text = re.sub(r"[_-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    words = []
    for word in text.split(" "):
        key = word.casefold()
        if key in _ACRONYMS:
            words.append(_ACRONYMS[key])
        elif key in _ALIASES:
            words.append(_ALIASES[key])
        elif word.isupper() and len(word) > 1:
            words.append(word.capitalize())
        else:
            words.append(word[:1].upper() + word[1:])
    return " ".join(words)


def humanize_action(action):
    raw = str(getattr(action, "value", action) or "").strip()
    if raw in _ACTIONS:
        return _ACTIONS[raw]
    if " " in raw and "_" not in raw and "." not in raw:
        # The action catalog already supplies curated labels such as "Wait for State".
        return " ".join(raw.split())
    if "." in raw:
        raw = raw.rsplit(".", 1)[-1]
    return humanize_identifier(raw) or "Perform Action"


def default_step_name(action, object_name, object_type=None):
    """Generate '[Action] [Object]', e.g. 'Click Watch Button'."""
    friendly_action = humanize_action(action)
    friendly_object = humanize_identifier(object_name, leaf=True) or "Object"
    if object_type is not None:
        try:
            kind = ObjectType(object_type)
        except ValueError:
            kind = None
        suffix = _KIND_SUFFIX.get(kind)
        has_suffix = bool(
            suffix and friendly_object.replace(" ", "").casefold().endswith(
                suffix.replace(" ", "").casefold()
            )
        )
        # An existing "Watch Button" is already meaningful for Toggle Button too.
        if suffix == "Toggle Button" and friendly_object.casefold().endswith("button"):
            has_suffix = True
        if suffix and not has_suffix:
            friendly_object += " " + suffix
    return "%s %s" % (friendly_action, friendly_object)


def default_name_for_call(call, repository=None):
    """Derive a display name without mutating a persisted StepCall."""
    if call.step_id == "gui.object.action":
        action = call.inputs.get("action", {})
        action_id = action.get("type", "action") if isinstance(action, dict) else action
    else:
        action_id = call.step_id
    reference = call.inputs.get("component_id")
    definition = None
    if reference and repository is not None and repository.contains(reference):
        definition = repository.get(reference)
    if reference is not None or definition is not None:
        name = definition.component_id if definition else reference
        kind = definition.object_type if definition else None
        return default_step_name(action_id, name, kind)
    return humanize_identifier(call.step_id.rsplit(".", 1)[-1]) or "Step"


def display_name_for_call(call, repository=None):
    """Never replace explicitly named calls, including user-authored capitalization."""
    return call.name.strip() if call.name and call.name.strip() else default_name_for_call(call, repository)
