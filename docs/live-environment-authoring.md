# Live environment authoring and regex object identity

## Runtime model

The authoring console is attached to the desktop environment that is already running. It does not configure, launch, stop, or otherwise own a target application. Closing the authoring console must not terminate applications in the environment.

Standalone execution may launch the environment first with an environment startup script:

```bash
automation-run plan run test.yaml \
  --backend attached-desktop \
  --components components.yaml \
  --environment-script ./start-environment.sh
```

Inside `automation-author`, **Run Test** skips the startup script and runs directly against the current desktop session. Application names may still be used as object-level locator properties where they improve identity, but they are not test-level execution targets.

New authoring project documents therefore persist repository/run paths and optional `environment_script` metadata, but do not persist a `target` block. Existing project files containing a legacy target block remain loadable.

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
