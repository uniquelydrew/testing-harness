# Live environment authoring and regex object identity

## Runtime model

The authoring console operates on the current desktop execution facility. It does not configure, launch, stop, or otherwise own a target application. Closing the authoring console must not terminate applications in the environment.

Application-specific setup is represented explicitly in the test flow. If setup requires a script, register a contract-backed script step and make that step the appropriate predecessor of the actions that depend on it:

```yaml
steps:
  - id: setup
    step: environment.prepare
    inputs:
      configuration: integration
    outputs:
      workspace: workspace

  - id: perform-action
    step: gui.object.action
    depends_on:
      - setup
    inputs:
      component_id: customer.save_button
      action:
        type: click
```

Standalone live-desktop execution can register the external implementation explicitly:

```bash
automation-run plan run test.yaml \
  --backend attached-desktop \
  --components components.yaml \
  --script-step ./script_steps/environment_prepare.yaml
```

`attached-desktop` remains accepted as a compatibility CLI backend selector, but it resolves to the targetless `live-desktop` execution backend.

Inside `automation-author`, **Run Test** executes the same plan against the current desktop session. If the first plan step prepares or launches applications, that preparation occurs as part of the test and is visible in execution state and evidence.

Application names may be used as object-level locator properties where they improve identity. They are not test-level execution targets. A single test may therefore interact with objects owned by multiple applications without declaring a multi-application target.

New authoring project documents persist repository/run paths and optional `script_steps` manifests. They do not persist a `target` block or `environment_script`. Existing project files containing those legacy fields remain readable during migration, but the target is ignored and an environment script is never executed out of plan.

See `docs/script-backed-steps.md` for the script protocol and manifest contract.

## Exact and regular-expression matching

Every string-valued object identity property can continue to use an exact value:

```yaml
identification:
  mandatory:
    accessible_id: result-row-42
    role: push button
```

A property can instead opt into regular-expression matching:

```yaml
identification:
  mandatory:
    accessible_id:
      regex: 'result-row-[0-9]+'
    role: push button
```

Regex matching uses full-property matching. For substring behavior, author the expression explicitly, for example `.*result-row-[0-9]+.*`. Role regex matching follows the same case-insensitive behavior as exact role matching.

The same matcher representation is honored by AT-SPI, Windows Java Access Bridge, and JavaFX bridge resolution. Nested JavaFX identity values such as application-defined `properties`, parent identities, and lineage entries may use the same regex leaf representation.

The Object Identity Workbench presents an **Exact / Regex** selector beside string-valued identity properties and validates the expression before saving.

## Click versus Activate

`Click` and `Activate` are intentionally distinct operations:

- **Click** resolves the object, obtains its screen bounds, and generates a desktop pointer click at the center of those bounds. It therefore works for resolvable objects even when the accessibility provider exposes no semantic action.
- **Activate** asks the accessibility/native framework to invoke the object's semantic default action (`doAction`, JavaFX `fire()`, and equivalent operations).

This means an object that can be highlighted from its resolved bounds can also be clicked from both the repository view and the Object Identity Workbench, provided it has positive screen bounds. Bounds-resolvable visual objects also expose Click even when they have no accessibility action.
