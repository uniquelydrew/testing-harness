#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3.12}"
RUFF_VERSION="0.14.0"
ARTIFACTS="${CI_LINT_ARTIFACTS_DIR:-$ROOT/artifacts/lint}"
mkdir -p "$ARTIFACTS"

# Isolate the linter so it cannot alter harness or test dependencies.
LINT_VENV="${CI_LINT_VENV:-$ROOT/.ruff-venv}"
"$PYTHON" -m venv "$LINT_VENV"
"$LINT_VENV/bin/python" -m pip install --disable-pip-version-check "ruff==$RUFF_VERSION"
RUFF="$LINT_VENV/bin/ruff"

"$RUFF" --version
# Collect *all* diagnostics for triage. An existing warning must not prevent
# the complete pytest suite from running while the baseline is being cleaned.
if "$RUFF" check --select ALL --output-format json . > "$ARTIFACTS/ruff-full.json"; then
    :
else
    status=$?
    if [[ "$status" -ne 1 ]]; then
        echo "Ruff inventory failed to execute (exit $status)" >&2
        exit "$status"
    fi
fi

echo "=== Full Ruff inventory (non-blocking during baseline triage) ==="
"$RUFF" check --select ALL --statistics . || test "$?" -eq 1

echo "=== Enforced correctness and resource-lifetime baseline ==="
"$RUFF" check --select E9,F821,F841,RUF059,RUF012,SIM115 .

echo "=== Historical screenshot lint regressions (scoped) ==="
# Only lint originally affected paths with these additional rules until the
# full repository-wide mechanical modernization has been reviewed.
"$RUFF" check --select UP031 \
    automation_harness/tests/test_codex_runtime.py \
    automation_harness/tests/test_component_catalog.py \
    automation_harness/tests/test_documentation.py \
    automation_harness/tests/test_execution_cli.py \
    automation_harness/tests/test_java_runtime_diagnostics.py \
    automation_harness/tests/test_java_target_launcher.py \
    automation_harness/tests/test_javafx_bridge_driver.py \
    automation_harness/tests/test_javafx_owner_window_preparation.py \
    automation_harness/tests/test_live_authoring_remediation.py \
    automation_harness/tests/test_object_identity_sync.py \
    automation_harness/tests/test_plan_repository_assignment.py \
    automation_harness/tests/test_pointer_click_runtime.py \
    automation_harness/tests/test_pytest_plan_adapter.py \
    automation_harness/tests/test_recording.py \
    automation_harness/utils/evidence.py

"$RUFF" check --select C408 \
    automation_harness/tests/test_compatibility_and_hierarchy.py \
    automation_harness/tests/test_recapture_safety.py \
    automation_harness/tests/test_test_object_factory.py \
    automation_harness/tests/test_window_orchestration.py

"$RUFF" check --select RUF015 automation_harness/tests/test_codex_runtime.py
"$RUFF" check --select SIM117 \
    automation_harness/tests/test_menu_combo_regression_unittest.py \
    automation_harness/utils/evidence.py
"$RUFF" check --select BLE001 automation_harness/tests/test_plan_visual_add_action_dialog.py
"$RUFF" check --select TRY004 automation_harness/utils/evidence.py
