from __future__ import annotations

import difflib
import json

from automation_harness.core.component_repository import ComponentRepository


def component_document(component_id, definition):
    """Return the persisted semantic form of one component definition."""
    return ComponentRepository({component_id: definition}).to_document()["components"][component_id]


def definitions_equal(component_id, left, right):
    """Compare definitions without file-origin metadata such as repository_path."""
    return component_document(component_id, left) == component_document(component_id, right)


def component_diff(component_id, current, incoming):
    """Render a stable unified diff for an object-repository merge conflict."""
    current_text = json.dumps(
        component_document(component_id, current),
        indent=2,
        sort_keys=True,
        default=str,
    ).splitlines()
    incoming_text = json.dumps(
        component_document(component_id, incoming),
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
