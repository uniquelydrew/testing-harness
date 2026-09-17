"""Compatibility helpers for migrating legacy dotted component aliases.

Repository UI lineage scopes were removed.  This module intentionally contains
no installer or GTK behavior; the pure rename helper remains for imports and
explicit legacy migrations.
"""
from __future__ import annotations


def _lineage_rename_map(repository, edits):
    """Return old-to-new aliases after explicitly requested legacy path edits."""
    edits = sorted(edits, key=lambda item: item[0].count("."))
    result = {}
    for component_id in repository.components:
        original_parts = component_id.split(".")
        current_parts = list(original_parts)
        for original_path, new_segment, _key in edits:
            path_parts = original_path.split(".")
            depth = len(path_parts)
            if len(original_parts) >= depth and original_parts[:depth] == path_parts:
                current_parts[depth - 1] = new_segment
        new_id = ".".join(current_parts)
        if new_id != component_id:
            result[component_id] = new_id
    return result
