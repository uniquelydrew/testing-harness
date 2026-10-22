# Pytest integration (initial implementation)

The optional `automation-pytest` command exposes saved `.ahplan` files as
native pytest items. pytest handles plan discovery, selection, results, and JUnit
export; the Automation Harness remains responsible for step execution, object
resolution, backend lifecycle, evidence, and its detailed HTML report.

## Install

Use the existing RHEL 8 / Python 3.6 harness environment and install the optional
pytest extra:

```bash
python -m pip install '.[pytest]'
```

The entry point installs the harness's Python 3.6 compatibility loader before
pytest imports the plugin. Do not invoke the integration using bare `pytest`
on Python 3.6.

## Discover plans

```bash
automation-pytest --collect-only -q tests/ersa/plans
```

A plan named `T1789.ahplan` is collected as one test item,
`T1789.ahplan::T1789`. Its internal managed steps are not separate pytest items.
Invalid plan syntax is reported during collection; no backend starts during
collection.

## Run plans

```bash
automation-pytest tests/ersa/plans \\
  --ah-backend=live-desktop \\
  --ah-project=tests/ersa/project.ahproject \\
  --ah-runs-dir=/tmp/ersa-pytest-runs \\
  --junitxml=/tmp/ersa-pytest-runs/junit.xml
```

`--ah-project` is optional. With a project, the plugin uses the project's step
registries and the plan's associated object repositories. Without a project,
portable reusable-step snapshots and plan-associated repositories are used.
Each selected plan must belong to the specified project.

For synthetic qualification without GTK, use a headless reference target:

```bash
automation-pytest tests/reference/plans --ah-backend=reference \\
  --ah-reference-mode=headless
```

Use repeated `--ah-var=NAME=VALUE` options to override plan variables; JSON
values are decoded, and other values are treated as strings. Each name must be
unique and a top-level variable identifier.

## Results

A plan's final harness exit code determines its pytest outcome. pytest JUnit
records include the harness run ID, artifact directory, and report path as test
properties. The original `run.json`, `events.jsonl`, `execution_state.json`,
and `report.html` remain the authoritative diagnostics.

## Scope and limitations

- The integration currently supports `live-desktop` and `reference` backends.
- pytest fixtures are **not** automatically injected into collected plan items.
  The harness's existing plan preparation and cleanup mechanisms still apply;
  unconditional fixture lifecycle support requires a later explicit contract.
- Only one worker may operate on a shared live desktop. The plugin rejects
  multiworker xdist operation for `live-desktop`; execution isolation is not
  implemented by this adapter.
- It does not rewrite TestPlan files or alter the core harness execution engine.
- The adapter is opt-in; normal `automation-run` and `automation-plan`
  commands have no pytest dependency.
