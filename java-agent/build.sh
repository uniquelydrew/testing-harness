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
if javac --help 2>&1 | grep -q -- '--release'; then
    JAVAC_LEVEL=(--release 8)
else
    JAVAC_VERSION="$(javac -version 2>&1 || true)"
    [[ "$JAVAC_VERSION" == "javac 1.8"* ]] || {
        echo "Java 8 agent requires JDK 8 javac or a newer javac with --release support; found: $JAVAC_VERSION" >&2
        exit 1
    }
    JAVAC_LEVEL=(-source 8 -target 8)
fi

# On JDK 9+ --release 8 constrains both bytecode and the linked Java API
# surface. Using only -source/-target with a newer JDK can accidentally compile
# calls to APIs that do not exist in the MSCT Java 8 runtime.
find "$SOURCE" -name '*.java' -print0 |
    xargs -0 javac "${JAVAC_LEVEL[@]}" -d "$CLASSES"

# Fail the build if the compiler silently emits anything newer than Java 8
# class-file version 52. This catches accidental target-level regressions.
while IFS= read -r -d '' class_file; do
    major="$(javap -verbose "$class_file" | awk '/major version:/ {print $3; exit}')"
    [[ "$major" == "52" ]] || {
        echo "Java agent class is not Java 8 bytecode: $class_file (major=$major)" >&2
        exit 1
    }
done < <(find "$CLASSES" -name '*.class' -print0)

cat > "$BUILD/MANIFEST.MF" <<'MANIFEST'
Manifest-Version: 1.0
Premain-Class: automation.harness.agent.AutomationAgent
Can-Redefine-Classes: false
Can-Retransform-Classes: false
MANIFEST
jar cfm "$BUILD/automation-harness-agent.jar" "$BUILD/MANIFEST.MF" -C "$CLASSES" .
echo "$BUILD/automation-harness-agent.jar"
