#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD="$ROOT/build"
CLASSES="$BUILD/classes"
rm -rf "$BUILD"
mkdir -p "$CLASSES"
find "$ROOT/src/main/java" -name '*.java' -print0 |
    xargs -0 javac --release 17 --add-modules jdk.httpserver -d "$CLASSES"
cat > "$BUILD/MANIFEST.MF" <<'MANIFEST'
Manifest-Version: 1.0
Premain-Class: automation.harness.agent.AutomationAgent
Add-Modules: jdk.httpserver
Can-Redefine-Classes: false
Can-Retransform-Classes: false
MANIFEST
jar cfm "$BUILD/automation-harness-agent.jar" "$BUILD/MANIFEST.MF" -C "$CLASSES" .
echo "$BUILD/automation-harness-agent.jar"
