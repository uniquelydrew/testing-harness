# Step Dataflow and Test Globals — 0.4.0

## Purpose

This increment adds UFT One-style reusable action parameters to the registered-step system without replacing normal Python semantics.

## Step inputs

Step inputs are derived from the registered function signature after the leading `ctx`/`context` parameter. The catalog records input name, parameter kind, annotation, whether it is required, and any default value.

## Step outputs

Steps declare stable output names in `@step(outputs=...)`.

```python
@step(
    "example.create",
    outputs={
        "result": "$",
        "identifier": "id",
        "status": "metadata.status",
    },
)
def create(ctx, ...):
    ...
```

`$` selects the complete return value. Dotted selectors traverse mappings, lists/tuples by integer segment, and object attributes.

A declared output is a contract: if its selector cannot be extracted from the step result, the step fails rather than publishing incomplete data.

## Output binding

`ctx.run_step()` accepts `bind_outputs`, mapping step output names to test-global variable names.

```python
ctx.run_step(
    "example.create",
    bind_outputs={"identifier": "current_id"},
)
```

The binding is recorded as `step_output_bound` evidence.

## Deferred input references

`ctx.ref("current_id")` creates a `VariableRef`. Registered invocation resolves references immediately before calling the step. References may appear directly or recursively inside lists, tuples, and dictionaries.

```python
ctx.run_step("example.consume", ctx.ref("current_id"))
```

This allows one registered action's output to feed another action's input without coupling the actions themselves.

## Test-global variables

`ctx.globals` is shared by all actions within one test and reset from initial values for every test.

Operations:

- `initialize(mapping, overwrite=False)`
- `get(path)` / `ctx.globals[path]`
- `set(name, value)` / `ctx.globals[name] = value`
- `update(name, mapping)`
- `append(name, value)`
- `extend(name, values)`
- `ref(path)` / `ctx.ref(path)`
- `snapshot()`

Bundle defaults are declared with `manifest.yaml:variables`. CLI `--var NAME=VALUE` overrides those defaults for a run. Initial values are JSON-compatible and deep-copied into each test context.

Variable mutation and resolution are structured evidence events, and pytest records `test_globals_final` at teardown.

## Static validation

Literal `bind_outputs={...}` calls are inspected before backend startup. The validator checks that:

- the referenced registered step exists;
- required backend capabilities are available;
- bound output names are declared by the step;
- bound targets are valid top-level global variable names;
- associated step-library output declarations are literal and inspectable.

This preserves the project's pre-protected-target feedback boundary: broken step dataflow is rejected before any target is started whenever it can be determined statically.
