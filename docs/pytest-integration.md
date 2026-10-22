# Pytest integration (experimental)

**Status:** Initial adapter implementation is committed on
`codex/pytest-plan-integration`. End-to-end Python 3.6 / RHEL GUI execution
has **not yet been qualified**. Treat this document as the implemented
interface and its known limitations, not proof that a GUI regression suite
has passed.

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

The plugin does not automatically assign the `ahplan` or `gui` pytest
markers to collected items; those names are registered for use by other pytest
tests. Use file paths or pytest `-k` for plan selection.

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

**Important:** A runtime preflight failure or invalid execution plan is
currently reported as a failed pytest item, not a distinct pytest
infrastructure error. More granular status classification is future work.

If pytest is invoked with `--junitxml=PATH`, each executed plan can publish:

- `harness_run_id`
- `harness_artifact_dir`
- `harness_report_html`

These are JUnit testcase properties pointing to the harness's own files.
The JUnit output is produced by pytest, **not** by copying an XML file out
of each harness run directory. Preserve both the JUnit file and the original
artifact directories in your CI artifact collection.

## Limitations and qualification

- **Experimental:** Unit/adapter regression tests have been added, but there
  is no confirmed passing Python 3.6 or RHEL live-GUI validation for this
  branch yet.
- **No implicit fixture injection:** Declarative `.ahplan` items do not
  automatically receive Python pytest fixtures or teardown behavior.
- **No guaranteed post-failure plan cleanup:** The existing managed plan
  runner stops on failed steps. Cleanup steps later in that same plan are
  not guaranteed to execute.
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
GUI-end-to-end readiness.
