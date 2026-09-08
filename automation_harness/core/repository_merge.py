from __future__ import annotations

import difflib
import json

from automation_harness.core.component_repository import ComponentRepository


def component_document(component_id, definition):
    """Return the persisted semantic form of one component definition."""
    return ComponentRepository({component_id: definition}).to_document()["components"][component_id]


def _display_component_document(component_id, definition):
    """Return merge-review data without exposing the internal immutable ID."""
    document = dict(component_document(component_id, definition))
    document.pop("object_id", None)
    return document


def definitions_equal(component_id, left, right):
    """Compare definitions without file-origin metadata such as repository_path."""
    return component_document(component_id, left) == component_document(component_id, right)


def component_diff(component_id, current, incoming):
    """Render a stable user-facing diff for an object-repository merge conflict."""
    current_text = json.dumps(
        _display_component_document(component_id, current),
        indent=2,
        sort_keys=True,
        default=str,
    ).splitlines()
    incoming_text = json.dumps(
        _display_component_document(component_id, incoming),
        indent=2,
        sort_keys=True,
        default=str,
    ).splitlines()
    return "\n".join(
        difflib.unified_diff(
            current_text,
            incoming_text,
            fromfile="current/%s" % component_id,
            tofile="incoming/%s" % component_id,
            lineterm="",
        )
    )
