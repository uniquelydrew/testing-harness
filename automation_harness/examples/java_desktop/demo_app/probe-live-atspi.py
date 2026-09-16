#!/usr/bin/env python3
"""Print a condensed AT-SPI tree for the Java desktop qualification target."""

import os

import pyatspi


DEMO_CLASS = os.environ.get("DEMO_CLASS", "DesktopDemo")
TARGET_TITLE = (
    "Automation Harness JavaFX Demo"
    if DEMO_CLASS == "FxOnlyDemo"
    else "Automation Harness Java Desktop Demo"
)
REQUIRED = (
    {"Follow JavaFX", "Demo progress", "Demo visual map"}
    if DEMO_CLASS == "FxOnlyDemo"
    else {"Follow Swing", "Tracking enabled", "JavaFX visual region"}
)
GENERIC_NAMES = {
    "application", "component", "content", "content pane", "filler", "frame",
    "jframe", "jfxpanel", "jlabel", "jpanel", "label", "pane", "panel",
    "root", "scene", "scroll pane", "stack pane", "vbox", "window",
}
SEMANTIC_ROLES = {
    "button", "check box", "combo box", "entry", "list item", "menu",
    "menu item", "progress bar", "radio button", "slider", "text",
    "toggle button", "window",
}
seen = set()


def _text(value):
    return str(value or "").strip()


def _meaningful(name, role, is_root):
    normalized_name = _text(name).casefold()
    normalized_role = _text(role).casefold()
    return (
        is_root
        or normalized_name in {item.casefold() for item in REQUIRED}
        or normalized_role in SEMANTIC_ROLES
        or normalized_name not in GENERIC_NAMES
    )


def walk(node, visible_depth=0, source_depth=0):
    try:
        name = _text(node.name)
        role = _text(node.getRoleName())
    except Exception:
        return
    if source_depth > 12:
        return
    is_root = source_depth == 0
    keep = _meaningful(name, role, is_root)
    if keep:
        print("%s- %s [%s]" % (
            "  " * visible_depth,
            name or "<unnamed>",
            role or "unknown",
        ))
        next_depth = visible_depth + 1
    else:
        next_depth = visible_depth
    if name in REQUIRED:
        try:
            bounds = node.queryComponent().getExtents(pyatspi.DESKTOP_COORDS)
            location = " bounds=(%s,%s,%s,%s)" % (
                bounds.x, bounds.y, bounds.width, bounds.height,
            )
        except Exception:
            location = ""
        print("%s  [required]%s" % ("  " * visible_depth, location))
        seen.add(name)
    for child in node:
        walk(child, next_depth, source_depth + 1)


print("CONDENSED AT-SPI TREE: %s" % TARGET_TITLE)
for application in pyatspi.Registry.getDesktop(0):
    application_name = _text(application.name)
    if (
        TARGET_TITLE in application_name
        or DEMO_CLASS in application_name
        or application_name.casefold() in {"java", "java application"}
    ):
        walk(application)

missing = REQUIRED - seen
if missing:
    raise SystemExit("missing: " + ", ".join(sorted(missing)))
