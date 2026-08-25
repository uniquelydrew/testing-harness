#!/usr/bin/env python3
"""Print live AT-SPI roles and bounds for the visible DesktopDemo window."""

import pyatspi

REQUIRED = {"Follow Swing", "Tracking enabled", "JavaFX visual region"}
seen: set[str] = set()


def walk(node: object, depth: int = 0) -> None:
    try:
        name = node.name or ""
        role = node.getRoleName()
    except Exception:
        return
    if name in REQUIRED:
        bounds = node.queryComponent().getExtents(pyatspi.DESKTOP_COORDS)
        print(f"{name}: role={role}; bounds=({bounds.x},{bounds.y},{bounds.width},{bounds.height})")
        seen.add(name)
    if depth < 12:
        for child in node:
            walk(child, depth + 1)


for application in pyatspi.Registry.getDesktop(0):
    if application.name == "Automation Harness Java Desktop Demo":
        walk(application)

missing = REQUIRED - seen
if missing:
    raise SystemExit("missing: " + ", ".join(sorted(missing)))
