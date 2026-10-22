#!/usr/bin/env bash
set -Eeuo pipefail

# Executed by scripts/ci/desktop-integration.sh after dependency installation.
# A manual self-hosted RHEL 8 desktop workflow exercises the pytest adapter
# against a real GUI reference backend without depending on ERSA assets.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ARTIFACTS="${CI_ARTIFACTS_DIR:-$ROOT/artifacts/desktop}"
mkdir -p "$ARTIFACTS"

TEMP="$(mktemp -d "${TMPDIR:-/tmp}/automation-pytest-desktop.XXXXXX")"
cleanup() {
  status=$?
  if [[ -d "$TEMP/runs" ]]; then
    cp -a "$TEMP/runs" "$ARTIFACTS/pytest-plan-runs" || true
  fi
  rm -rf -- "$TEMP"
  return "$status"
}
trap cleanup EXIT

cat > "$TEMP/gui-reference-smoke.ahplan" <<'YAML'
name: pytest-gui-reference-smoke
version: 1
variables:
  expected: 42
objects: {}
step_definitions: {}
steps:
  - id: verify-gui-reference-execution
    step: validation.equal
    inputs:
      name: python36-gui-reference
      actual: {$var: expected}
      expected: 42
YAML

# --reference-display=virtual starts an isolated Xvfb-backed GUI rather than
# interacting with the runner's existing desktop or target application.
automation-pytest "$TEMP/gui-reference-smoke.ahplan" \
  --ah-backend=reference \
  --ah-reference-mode=gui \
  --ah-reference-display=virtual \
  --ah-runs-dir="$TEMP/runs" \
  --junitxml="$ARTIFACTS/pytest-plan.xml"
