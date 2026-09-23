#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

unset JAVA_TOOL_OPTIONS

bash java-agent/build.sh
bash javafx_agent/build.sh

test -f java-agent/build/automation-harness-agent.jar
test -f javafx_agent/build/automation-harness-javafx-agent.jar
