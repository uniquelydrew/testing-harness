#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD="$ROOT/build"
CLASSES="$BUILD/classes"
rm -rf "$BUILD"
mkdir -p "$CLASSES"
find "$ROOT/src/main/java" -name '*.java' -print0 | xargs -0 javac -source 11 -target 11 -d "$CLASSES"
cat > "$BUILD/MANIFEST.MF" <<'MANIFEST'
Manifest-Version: 1.0
Premain-Class: com.automationharness.javafx.AutomationHarnessJavaFxAgent
Can-Redefine-Classes: false
Can-Retransform-Classes: false
MANIFEST
jar cfm "$BUILD/automation-harness-javafx-agent.jar" "$BUILD/MANIFEST.MF" -C "$CLASSES" .
echo "$BUILD/automation-harness-javafx-agent.jar"
