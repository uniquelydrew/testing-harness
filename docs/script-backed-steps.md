# Script-backed steps

Script implementations are first-class semantic steps. They use the same plan input/output bindings, dependency graph, variable store, evidence stream, risk metadata, and qualification catalog as native registered steps.

A script is **not** an out-of-band preflight hook. If a script prepares the application environment, that preparation belongs in the test flow as a step.

## Step manifest

```yaml
version: 1
id: environment.prepare
description: Start and prepare the application environment.
risk: application_control

inputs:
  configuration:
    type: str
    required: true
  seed:
    type: int
    required: false
    default: 0

outputs:
  application_pid:
    type: int
  workspace:
    type: str

implementation:
  kind: script
  path: prepare_environment.py
  interpreter: python
  timeout: 60
```

The manifest is the authoritative contract. Inputs not declared by the manifest are rejected. Required inputs must be present and compatible with their declared types. A script may only return declared outputs, and required outputs must be present and type-compatible before any output binding is committed.

Supported scalar contract names include `str`, `bool`, `int`, `float`, `number`, `Any`, and `None`. Mapping/list forms and optional/union forms are also accepted. Project-specific or unknown type names remain runtime-defined rather than being falsely rejected by the generic contract checker.

## Process protocol

The harness sends one JSON document to the script on standard input:

```json
{
  "protocol": 1,
  "step": "environment.prepare",
  "inputs": {
    "configuration": "integration",
    "seed": 0
  }
}
```

A successful script writes exactly one JSON response to standard output:

```json
{
  "protocol": 1,
  "outputs": {
    "application_pid": 48129,
    "workspace": "/tmp/test-48129"
  }
}
```

Standard error is retained as execution evidence. A non-zero process exit, timeout, malformed JSON, protocol mismatch, undeclared output, missing required output, or output type mismatch fails the step. Outputs are validated before being committed to test variables.

## Use in a test plan

```yaml
name: customer-update
version: 1
variables: {}
steps:
  - id: setup
    step: environment.prepare
    inputs:
      configuration: integration
    outputs:
      application_pid: application_pid
      workspace: workspace

  - id: save-customer
    step: gui.object.action
    depends_on:
      - setup
    inputs:
      component_id: customer.save_button
      action:
        type: click
```

The setup step is ordinary plan behavior. It may produce values used by later steps, participates in dependency ordering, appears in execution state, and is included in the evidence stream.

## Register implementations with an authoring project

```yaml
version: 1
name: Customer integration tests
repository: components.yaml
runs_dir: runs
script_steps:
  - script_steps/environment_prepare.yaml
```

Paths are relative to the project manifest. Loading the project registers those semantic step definitions before plans are validated or executed.

For standalone live-desktop execution, manifests can also be registered with repeatable `--script-step` arguments.

## Targetless execution

Projects and tests do not identify a target application. The live desktop backend only describes the execution facility. Application, window, parent, and component ownership belong to object identity/lineage supplied by the object repository or another external object metadata provider.

This means one test may naturally operate across multiple applications. The resolver uses each referenced object's own lineage instead of injecting a test-level application into every locator.

Legacy `target` fields may still be read for compatibility but are not persisted or used to scope execution. Legacy `environment_script` fields are never executed; they must be migrated to a contract-backed script step placed explicitly in the test flow.

## Qualification and evidence

The script file is SHA-256 hashed when its manifest is registered. That digest is incorporated into the registered step implementation identity, so changing the external script changes the step catalog qualification hash. Script execution evidence includes the manifest path, script path, interpreter, implementation digest, validated outputs, and captured standard error.

## Lifecycle boundary

The desktop/backend must exist before plan execution can begin. Application-specific setup belongs in steps. Examples include launching the application, copying configuration, seeding data, starting application support services, or creating test workspaces.

Backend/session bootstrap remains infrastructure: establishing the desktop session, creating run artifacts, connecting accessibility facilities, and initializing the executor happen before the first step.

Guaranteed cleanup/finalizer semantics for resource-producing steps are a separate lifecycle feature and are not implemented by the initial script-step contract.
