#!/usr/bin/env bash
# Stable Jenkins-facing launcher for the supported CI lanes.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LANE="${1:-}"

usage() {
  cat >&2 <<'USAGE'
usage: scripts/ci/jenkins.sh {unit|agents|desktop}

unit     Install test dependencies, run the Python suite, and publish JUnit XML.
agents   Build the Swing/JOGL and JavaFX instrumentation agents.
desktop  Run the explicit RHEL 8 desktop qualification lane.
USAGE
}

case "$LANE" in
  unit)
    export CI_ARTIFACTS_DIR="${CI_ARTIFACTS_DIR:-$ROOT/artifacts/jenkins/unit}"
    exec bash "$ROOT/scripts/ci/unit.sh"
    ;;
  agents)
    export CI_ARTIFACTS_DIR="${CI_ARTIFACTS_DIR:-$ROOT/artifacts/jenkins/agents}"
    mkdir -p "$CI_ARTIFACTS_DIR"
    exec bash "$ROOT/scripts/ci/build-agents.sh"
    ;;
  desktop)
    export CI_ARTIFACTS_DIR="${CI_ARTIFACTS_DIR:-$ROOT/artifacts/jenkins/desktop}"
    bash "$ROOT/scripts/ci/desktop-preflight.sh"
    exec bash "$ROOT/scripts/ci/desktop-integration.sh"
    ;;
  -h|--help|help|'')
    usage
    exit 2
    ;;
  *)
    printf 'jenkins launcher: unknown lane: %s\n' "$LANE" >&2
    usage
    exit 2
    ;;
esac
