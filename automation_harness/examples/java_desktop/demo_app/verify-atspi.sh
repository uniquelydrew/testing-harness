#!/usr/bin/env bash
set -euo pipefail

export DISPLAY=:99
demo_class="${1:-DesktopDemo}"
export DEMO_CLASS="$demo_class"
export JAVA_TOOL_OPTIONS="-Djavax.accessibility.assistive_technologies=org.GNOME.Accessibility.AtkWrapper"
Xvfb "$DISPLAY" -screen 0 1280x900x24 >/tmp/java-desktop-demo-xvfb.log 2>&1 &
xvfb_pid=$!
java -Xbootclasspath/a:/usr/share/java/java-atk-wrapper.jar -Dglass.accessible.force=true --module-path /usr/share/openjfx/lib --add-modules javafx.controls,javafx.swing \
  -cp build/classes "com.automationharness.demo.${demo_class}" >/tmp/java-desktop-demo.log 2>&1 &
java_pid=$!
cleanup() { kill "$java_pid" "$xvfb_pid" 2>/dev/null || true; }
trap cleanup EXIT
sleep 6

python3 - <<'PY'
import os
import pyatspi

required = {"Follow JavaFX", "Demo progress", "Demo visual map"}
if os.environ["DEMO_CLASS"] == "DesktopDemo":
    required = {"Follow Swing", "Tracking enabled", "JavaFX visual region"}
seen = set()

def walk(node, depth=0):
    try:
        name = node.name or ""
        role = node.getRoleName()
    except Exception:
        return
    if depth < 4:
        print(f"NODE depth={depth} name={name!r} role={role!r}")
    if name in required:
        try:
            bounds = node.queryComponent().getExtents(pyatspi.DESKTOP_COORDS)
            location = f" bounds=({bounds.x},{bounds.y},{bounds.width},{bounds.height})"
        except Exception:
            location = ""
        print(f"FOUND {name!r} role={role!r}{location}")
        seen.add(name)
    if depth < 12:
        for child in node:
            walk(child, depth + 1)

for app in pyatspi.Registry.getDesktop(0):
    print(f"APPLICATION {app.name!r}")
    if "DesktopDemo" in app.name or "java" in app.name.casefold():
        walk(app)
missing = required - seen
if missing:
    raise SystemExit("missing accessible objects: " + ", ".join(sorted(missing)))
PY
