#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PYTHON="${PYTHON:-python3.12}"

source /etc/os-release
test "${ID:-}" = "rhel"
test "${VERSION_ID%%.*}" = "8"

"$PYTHON" - <<'PY'
import sys
assert sys.version_info[:2] == (3, 12), sys.version
PY

command -v java >/dev/null
command -v javac >/dev/null
command -v jar >/dev/null
command -v Xvfb >/dev/null

"$PYTHON" - <<'PY'
for module in ("gi", "cairo", "pyatspi"):
    __import__(module)
PY
