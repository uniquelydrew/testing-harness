#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

ARTIFACTS="${CI_ARTIFACTS_DIR:-$ROOT/artifacts/desktop}"
mkdir -p "$ARTIFACTS"

cleanup() {
  if [[ -n "${XVFB_PID:-}" ]]; then
    kill "$XVFB_PID" >/dev/null 2>&1 || true
    wait "$XVFB_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

if [[ -z "${DISPLAY:-}" ]]; then
  export DISPLAY=:99
  Xvfb "$DISPLAY" -screen 0 1280x800x24 -nolisten tcp -ac >"$ARTIFACTS/xvfb.log" 2>&1 &
  XVFB_PID=$!
  for _ in $(seq 1 50); do
    [[ -S /tmp/.X11-unix/X99 ]] && break
    sleep .1
  done
fi

python3 -m pip install --upgrade 'pip==21.3.1' 'setuptools==59.6.0' 'wheel==0.37.1'
python3 -m pip install -e '.[test,vision]'

bash scripts/ci/build-agents.sh

python3 -m pytest -m "ui or integration" --junitxml="$ARTIFACTS/pytest.xml"
automation-run selftest --reference-display virtual 2>&1 | tee "$ARTIFACTS/selftest.log"
automation-run selftest --require-atspi --reference-display virtual 2>&1 | tee "$ARTIFACTS/selftest-atspi.log"
