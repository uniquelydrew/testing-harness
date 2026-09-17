#!/usr/bin/env bash
set -euo pipefail
# Never inject this agent into the javac/jar processes rebuilding its own JAR.
unset JAVA_TOOL_OPTIONS
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD="$ROOT/build"
CLASSES="$BUILD/classes"
SOURCE="$ROOT/src/java8/java"
rm -rf "$BUILD"
mkdir -p "$CLASSES"

# MSCT is deployed on Java 8. Compile the instrumentation agent to Java 8
# bytecode so the same JAR can be loaded by Java 8 and newer target JVMs.
# com.sun.net.httpserver is part of JDK 8; it does not require the Java 9+
# module-system --add-modules option.
find "$SOURCE" -name '*.java' -print0 |
    xargs -0 javac -source 8 -target 8 -d "$CLASSES"
cat > "$BUILD/MANIFEST.MF" <<'MANIFEST'
Manifest-Version: 1.0
Premain-Class: automation.harness.agent.AutomationAgent
Can-Redefine-Classes: false
Can-Retransform-Classes: false
MANIFEST
jar cfm "$BUILD/automation-harness-agent.jar" "$BUILD/MANIFEST.MF" -C "$CLASSES" .
echo "$BUILD/automation-harness-agent.jar"
