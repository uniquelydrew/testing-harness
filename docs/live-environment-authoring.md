# Live environment authoring and regex object identity

## Runtime model

The authoring console operates on the current desktop execution facility. It does not configure, launch, stop, or otherwise own an application. Closing the authoring console must not terminate applications in the environment.

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

Standalone execution can register the external implementation explicitly:

```bash
automation-run plan run test.ahplan \
  --backend live-desktop \
  --components objects.ahobjects
```

Script-step manifests used by the authoring project are registered when the
project is loaded. A self-contained saved plan embeds the object and step
definitions it needs; `--components` is only an overlay.

Self-contained plans can be executed directly. The longer
`automation-run plan run` spelling remains supported:

```bash
automation-plan checkout.ahplan --backend live-desktop
```

## YAML-backed artifact file types

New authoring saves use distinct compound extensions so a file's role is
visible without opening it. Every artifact remains human-readable YAML, and
legacy `.yaml` and `.yml` files remain loadable.

| Artifact | Extension |
| --- | --- |
| Test project | `.ahproject` |
| Test plan | `.ahplan` |
| Object repository | `.ahobjects` |
| Script-step manifest | `.ahstep` |

New projects begin with an empty `objects.ahobjects`. Saving a test plan
embeds the definitions for every literal `component_id` referenced by its
steps. CLI execution uses these inline objects by default; `--components`
overlays an external repository when an explicit override is needed.

`live-desktop` represents only the current desktop session and available interaction facilities. It does not select an application.

Inside `automation-author`, **Run Test** executes the same plan against the current desktop session. If the first plan step prepares or launches applications, that preparation occurs as part of the test and is visible in execution state and evidence.

Application names may be used as object-level locator properties where they improve identity. They are not test-level execution scope. A single test may therefore interact with objects owned by multiple applications without declaring any global application selector.

Authoring project documents persist repository/run paths and optional `script_steps` manifests only. Environment preparation belongs in the plan.

See `docs/script-backed-steps.md` for the script protocol and manifest contract.

## Capture and recording consistency

Capture Next Click and recording resolve the same semantic component boundary.
For instrumented JavaFX applications the native bridge result wins bounded
arbitration over the generic AT-SPI observation. For GTK and Swing, AT-SPI
promotes presentation leaves to their nearest actionable owner.

Use Capture Next Click for an isolated object. Use recording for interactions
whose intermediate state must remain open, such as menu and submenu navigation.
Standard menu descendants are persisted under their menu component and executed
as one atomic `select_menu_item` path.

Stopping a recording is asynchronous. When adapter shutdown completes, the
Object Identity Workbench opens with all distinct interacted semantic targets
checked. The workbench groups targets by window and omits raw panel/filler/text
ancestry; repeated observations of one durable object remain one checked row.

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
