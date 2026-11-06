#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3.12}"
ARTIFACTS="${CI_ARTIFACTS_DIR:-$ROOT/artifacts/unit}"
mkdir -p "$ARTIFACTS"

"$PYTHON" -m pip install --upgrade pip
"$PYTHON" -m pip install -e '.[test]'
"$PYTHON" -m pytest --tb=long --junitxml="$ARTIFACTS/pytest.xml"
