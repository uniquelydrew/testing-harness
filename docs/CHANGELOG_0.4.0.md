# 0.4.0

## Step parameter contracts

- Registered function parameters are cataloged as named step inputs.
- `@step(outputs=...)` declares stable named outputs and selectors.
- `$` selects the complete return value; dotted selectors traverse nested results.
- `automation-run steps describe` now prints input and output contracts.
- `registered_steps.json` records machine-readable inputs and outputs for every execution.

## Step-to-step dataflow

- `ctx.run_step(..., bind_outputs={...})` routes named step outputs into test-global variables.
- `ctx.ref("name")` creates a deferred reference resolved immediately before a later registered step runs.
- Variable references resolve recursively inside lists, tuples, and mappings.
- Literal output bindings are statically validated before backend startup.

## Test-global variables

- Added `ctx.globals`, isolated per pytest test and shared across all steps in that test.
- Supports initialize, get/set, mapping update, list append/extend, nested references, and snapshots.
- `manifest.yaml:variables` initializes bundle defaults.
- `automation-run run --var NAME=VALUE` overrides defaults at run time.
- Every variable initialization/read/resolution/mutation and step-output binding is recorded as structured evidence.
- Final variable state is recorded as `test_globals_final` at test teardown.

## Verification

- Framework tests: 26 passed from source and fresh wheel installation.
- Reference service/registry/dataflow suite: 8 passed.
- Reference UI suite on build host: 1 passed, 1 skipped because `pyatspi` is unavailable.
- CLI variable override integration: passed.
- Zero-byte package files: none.
- Residual mock/no-op scan: none; only intentional abstract `ExecutionBackend` methods contain `NotImplementedError`.
