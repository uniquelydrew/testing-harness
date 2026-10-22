# Pytest integration (experimental)

**Status:** The adapter's Python 3.6 CI test job has passed, including
real headless reference-backend execution and failure scenarios.
RHEL 8.9 live-GUI execution remains **unqualified**. The adapter is opt-in,
and its support for a shared desktop remains intentionally serial.

## Responsibility boundary

The optional `automation-pytest` command discovers saved `.ahplan` files
and represents each file as one pytest test item. It does **not** convert
individual plan steps into pytest items.

| Pytest integration | Automation Harness |
| --- | --- |
| Discover/select plans and assign test IDs | Load and validate TestPlans |
| Execute one collected plan through a small adapter | Execute all steps through `execute_plan()` |
| Provide pytest pass/fail status and optional JUnit XML | Maintain object resolution, backends, evidence, and step ordering |
| Export harness artifact paths as pytest properties | Generate `run.json`, `events.jsonl`, `execution_state.json`, and `report.html` |

The regular `automation-plan` and `automation-run` CLIs do not depend on
pytest. The existing Python bundle pytest mode (`automation-run run`) is
also separate from the new declarative plan collector.

## Installation

The repository's Python 3.6/RHEL 8 bootstrap installs the core harness but
does not install pytest automatically. In the activated harness environment,
from the repository root:

```bash
python -m pip install -e '.[pytest]'
```

The optional extra declares `pytest>=6.2,<7.0`. For Python 3.6, use
`automation-pytest` rather than bare `pytest`; its entry point installs
the harness's Python 3.6 compatibility loader before pytest imports the
adapter. Other test plugins are not required.

## Discovering and selecting plans

Examples below use placeholder paths representing privately maintained ERSA
assets; those files do **not** ship in the public repository.

```bash
automation-pytest --collect-only -q ./tests/ersa/plans
automation-pytest -q ./tests/ersa/plans/T1789.ahplan
automation-pytest -k T1789 --collect-only ./tests/ersa/plans
```

A file named `T1789.ahplan` appears as
`T1789.ahplan::T1789`. The filename, not its editable human-readable plan
name, defines the collected item ID.

pytest collection loads the plan to check the schema. Syntax/schema failures
are reported as collection errors. Resource resolution, backend qualification,
and GUI execution occur later, during the test's run phase. Collection-only
runs do not start an execution backend.

Every collected plan is marked `ahplan`. The `gui` marker is also applied
when the selected backend is `live-desktop`. For example, select only
declarative plans using `-m ahplan`, or filter by filename with `-k`.

## Running plans

```bash
automation-pytest ./tests/ersa/plans \
  --ah-backend=live-desktop \
  --ah-project=./tests/ersa/project.ahproject \
  --ah-runs-dir="$HOME/.local/state/automation-harness/pytest-runs" \
  --junitxml="$HOME/.local/state/automation-harness/pytest-junit.xml"
```

The harness runtime path rules still apply: do not place execution artifact
directories inside the source checkout.

A synthetic reference backend can be selected explicitly:

```bash
automation-pytest ./tests/reference/plans \
  --ah-backend=reference \
  --ah-reference-mode=headless
```

The requested plans must contain steps supported by the selected backend.
Headless mode does not support GUI-only actions.

### Supported adapter options

| Option | Default | Meaning |
| --- | --- | --- |
| `--ah-backend` | `live-desktop` | Choose `live-desktop` or `reference` |
| `--ah-project=PATH` | None | Load a version-2 `.ahproject` and require each selected plan to be a member |
| `--ah-runs-dir=PATH` | External harness runtime runs directory | Base directory for per-plan harness artifacts |
| `--ah-var=NAME=VALUE` | None | Repeated top-level variable overrides; JSON values are decoded |
| `--ah-reference-mode` | `gui` | `gui` or `headless` for the reference backend |
| `--ah-reference-display` | `virtual` | `virtual`, `native`, or `auto` reference display selection |
| `--ah-setup-command=COMMAND` | None | Optional executable and arguments run before each plan |
| `--ah-teardown-command=COMMAND` | None | Optional executable and arguments run in `finally` after each plan |
| `--ah-lifecycle-timeout=SECONDS` | `90` | Positive per-command timeout; failures fail the pytest item |

The usual pytest flags also apply, including `--collect-only`, `-k`,
`-q`, and `--junitxml`.

`--ah-var` names must be unique and must not contain dots. For example:

```bash
automation-pytest ./tests/ersa/plans/T1789.ahplan \
  --ah-var='site=42' \
  --ah-var='camera="North"' \
  --ah-var='enabled=true'
```

JSON literals become their native types. Non-JSON values remain strings.

## Explicit application lifecycle

The harness's `live-desktop` backend does **not** own the application
lifecycle. For external GUI applications, use explicit setup and teardown
commands around each collected plan when a guaranteed cleanup action is needed:

```bash
automation-pytest ./tests/ersa/plans \
  --ah-backend=live-desktop \
  --ah-setup-command='bash extensions/applications/ersa/launch.sh' \
  --ah-teardown-command='bash extensions/applications/ersa/stop.sh' \
  --ah-lifecycle-timeout=90
```

These example paths refer to private integration assets, not files shipped by
the public harness. Commands run with the pytest root directory as their
working directory and inherit the process environment (including
`DISPLAY`, D-Bus credentials, and `XAUTHORITY` where available).
Arguments are split with `shlex` and passed directly to the executable:
**no shell is spawned automatically**. Use `bash path/to/script.sh` if a
shell script requires it. Shell operators, redirects, and environment
expansion are not interpreted by the adapter.

When configured, teardown runs after a passing test, after a failed managed
step, or after a setup failure. Command errors and timeouts fail the pytest
item; if both execution and teardown fail, both reasons are reported.
Command stdout/stderr are attached to pytest report sections when the
command succeeds. A successful teardown does not imply that a specific
application state was restored; lifecycle scripts must enforce their own
idempotency and environmental guarantees.

This is **external process lifecycle**, not pytest fixture injection or
a guarantee that cleanup steps embedded *later in the TestPlan* will run.
Without a configured teardown command, the harness's managed execution
early-stop behavior is unchanged.

## Project and resource resolution

Without `--ah-project`:
- The adapter loads the self-contained TestPlan.
- It resolves any object repositories explicitly associated with the plan,
  relative to the plan file.
- It uses the plan's snapshotted reusable-step definitions, when present.

With `--ah-project`:
- It loads the project's reusable-step registries and their object repositories.
- It overlays plan-associated repositories and registry repositories using the
  same shared `resolve_plan_resources()` function as Project Manager batch
  execution.
- It rejects a selected plan not listed in that project's test-plan membership.

`execute_plan()` remains responsible for validating the final merged
repository and expanded plan. The plugin does not write to plan or repository
files during collection.

## Result mapping and artifacts

Each collected plan invokes the existing `execute_plan()` exactly once.
The adapter reads its `RunResult` and emits a pytest result:

| Harness outcome | Current pytest outcome |
| --- | --- |
| Exit code `0` | Passed |
| Any nonzero exit code (`1`, `2`, `3`) | Failed pytest item with the harness diagnostics |
| Invalid `.ahplan` during discovery | Collection error |

**Important:** All nonzero harness exit codes still fail the pytest item,
but the adapter now records a stable `harness_result_class` property to
distinguish `step_failure` (1), `qualification_failure` (2), and
`runtime_error` (3). This is metadata for CI consumers, **not** a change
to pytest's failure/error phases. Unexpected adapter exceptions use
`adapter_error`, and teardown failures set `harness_teardown=failed`.

If pytest is invoked with `--junitxml=PATH`, each executed plan can publish:

- `harness_run_id`
- `harness_exit_code`
- `harness_result_class`
- `harness_artifact_dir`
- `harness_report_html`
- `harness_teardown` (only when teardown fails)

These are JUnit testcase properties pointing to the harness's own files.
The JUnit output is produced by pytest, **not** by copying an XML file out
of each harness run directory. Preserve both the JUnit file and the original
artifact directories in your CI artifact collection.

## Limitations and qualification

- **RHEL GUI not yet qualified:** Python 3.6 CI passes with the real
  headless reference backend, but X11/AT-SPI/JavaFX/ERSA live-desktop
  execution has not been qualified on RHEL 8.9.
- **No implicit fixture injection:** Declarative `.ahplan` items do not
  automatically receive Python pytest fixtures. Teardown is explicit via
  `--ah-teardown-command`.
- **Managed-plan cleanup still conditional:** The existing managed plan
  runner stops on failed steps. Cleanup steps later in the plan are not
  guaranteed to execute; use external teardown for guaranteed invocation.
- **No shared-display parallel execution:** The plugin rejects xdist
  multiworker use with `live-desktop` if the worker count is exposed by
  pytest. It does not implement per-worker display/application isolation.
- **No pytest replacement for harness internals:** Plan ordering, retry and
  completion semantics, object lineage, screenshots, and GUI interaction
  continue to live in the harness.
- **No automatic migration:** Existing Python test bundles continue using
  `automation-run run`, and saved TestPlans require no format changes.

### Adapter regression test command

Once the optional pytest dependencies are installed, run:

```bash
python -m pytest -q automation_harness/tests/test_pytest_plan_adapter.py
```

This command targets the repository's Python test suite; `automation-pytest`
is the intended command for executing discovered `.ahplan` files on the
RHEL/Python 3.6 runtime. Passing adapter tests alone does not demonstrate
RHEL GUI-end-to-end readiness.
