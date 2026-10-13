"""Best-effort pre-execution component evidence.

No interaction, activation, or authored assertion occurs here. Acquisition is
synchronous before dispatch; failures are represented as diagnostic evidence.
"""
from __future__ import annotations

from pathlib import Path

from automation_harness.models.step_evidence import (
    ComponentEvidence, StepEvidenceSummary, referenced_components,
)


def collect_pre_step_evidence(context, node_id, inputs, *, attempt_id="attempt-001", margin=16):
    """Capture with a 16px default context margin, clipped to the desktop."""
    if not isinstance(margin, int) or margin < 0:
        raise ValueError("capture margin must be a non-negative integer")
    known = context.components.components
    targets = referenced_components(inputs, known)
    if not targets:
        return StepEvidenceSummary(str(node_id), attempt_id)

    bounds_by_id = {}
    errors = {}
    for component_id in targets:
        try:
            resolved = context.component(component_id).resolve()
            bounds = resolved.metadata.get("bounds")
            if not isinstance(bounds, (tuple, list)) or len(bounds) != 4:
                raise ValueError("resolved component has no desktop bounds")
            x, y, w, h = (int(value) for value in bounds)
            if w <= 0 or h <= 0:
                raise ValueError("resolved component has invalid dimensions")
            bounds_by_id[component_id] = (x, y, w, h)
        except Exception as exc:
            errors[component_id] = "%s: %s" % (type(exc).__name__, exc)

    snapshot = None
    if bounds_by_id:
        try:
            from PIL import ImageGrab
            snapshot = ImageGrab.grab()
        except Exception as exc:
            for component_id in bounds_by_id:
                errors[component_id] = "screen acquisition: %s: %s" % (type(exc).__name__, exc)

    evidence = []
    output_dir = context.run_dir / "evidence" / "steps" / _safe(node_id) / _safe(attempt_id)
    for index, component_id in enumerate(targets, 1):
        if component_id in errors:
            evidence.append(ComponentEvidence(component_id, "unavailable", diagnostic=errors[component_id]))
            continue
        x, y, w, h = bounds_by_id[component_id]
        left, top = max(0, x - margin), max(0, y - margin)
        right, bottom = min(snapshot.width, x + w + margin), min(snapshot.height, y + h + margin)
        if x < 0 or y < 0 or x + w > snapshot.width or y + h > snapshot.height or right <= left or bottom <= top:
            evidence.append(ComponentEvidence(component_id, "unavailable", diagnostic="component bounds outside captured desktop"))
            continue
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            path = output_dir / ("component-%03d.png" % index)
            snapshot.crop((left, top, right, bottom)).save(path)
            evidence.append(ComponentEvidence(component_id, "captured",
                                              image_path=path.relative_to(context.run_dir).as_posix()))
        except Exception as exc:
            evidence.append(ComponentEvidence(component_id, "unavailable",
                                              diagnostic="image persistence: %s: %s" % (type(exc).__name__, exc)))
    return StepEvidenceSummary(str(node_id), attempt_id, tuple(evidence))


def _safe(value):
    text = "".join(c if c.isalnum() or c in "-_" else "-" for c in str(value))
    return text[:100] or "unnamed"
