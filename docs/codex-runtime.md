# Codex runtime orchestration

`automation-codex-runtime` is the machine-facing entrypoint for launching the
authoring application during Codex-assisted investigation. It deliberately
separates the lifetime of the graphical application from the lifetime of the
shell or `codex exec` invocation that launched it.

After `bash bootstrap.sh`, use:

```bash
automation-codex-runtime launch
```

From a source checkout, the equivalent stable wrapper is:

```bash
bash scripts/codex-runtime launch
```

For a crash that occurs before normal Python logging can run, launch under the
orchestrator's batch GDB mode:

```bash
bash scripts/codex-runtime launch --gdb
```

If the application terminates natively, the session also contains
`gdb-backtrace.txt`. Use this mode for reproducible `SIGSEGV` failures; do
not replace it with a direct `gdb automation-author` invocation because that
would bypass the runtime handoff and monitoring contract.

Arguments after `--` are forwarded to `automation-author`:

```bash
automation-codex-runtime launch -- --repository ./objects.ahobjects
```

## Codex contract

A Codex task that needs the live GUI should be directed to this entrypoint
instead of starting `automation-author` itself. A suitable machine-facing
instruction is:

```text
Launch the Automation Harness with:
  bash scripts/codex-runtime launch
Use bash scripts/codex-runtime status/snapshot/report to inspect it.
Do not replace the orchestrator with a direct automation-author invocation.
If manual GUI interaction is required, leave the application running and
report the active session directory.
```

The launch command starts a detached supervisor and returns a JSON manifest.
The supervisor starts the GUI in its own process group, so the GUI remains
alive when the initiating `codex exec` exits.

Only one active orchestrated authoring session is permitted. A second launch
returns the existing running manifest rather than creating competing native
AT-SPI/GTK sessions.

## Commands

```bash
automation-codex-runtime launch
automation-codex-runtime status
automation-codex-runtime snapshot
automation-codex-runtime report
automation-codex-runtime stop
```

- **launch** performs desktop/native preflight, creates a session, starts the
  authoring GUI, and begins runtime monitoring.
- **status** prints the authoritative active-session manifest as JSON.
- **snapshot** captures current process status, process tree, selected `/proc`
  state, and environment metadata without stopping the application.
- **report** prints the paths Codex should inspect after a reproduction.
- **stop** writes a stop request; the supervisor terminates the GUI process
  group and records the resulting exit state.

Do not use `stop` while a human is expected to reproduce a GUI problem.

## Runtime evidence

The default root is:

```text
$XDG_STATE_HOME/automation-harness/codex-runtime
```

or, when `XDG_STATE_HOME` is unset:

```text
~/.local/state/automation-harness/codex-runtime
```

Set `AUTOMATION_HARNESS_CODEX_RUNTIME_DIR` to override it.

Each launch creates a timestamped session containing:

```text
manifest.json
environment.json
preflight.json
runtime.jsonl
stdout.log
stderr.log
supervisor.log
process-tree-final.txt
snapshot-*/
```

`manifest.json` is the authoritative handoff. It records the GUI PID/process
group, supervisor PID, launch command, working directory, state, timestamps,
artifact paths, and final return code or terminating signal.

`runtime.jsonl` records application start/finish and periodic process
heartbeats including Linux process state, RSS/virtual memory, thread count,
and descriptor-table size. A negative subprocess return code is normalized
into the terminating signal in the final manifest, making native crashes
distinguishable from ordinary nonzero application exits.

`environment.json` captures the display/session variables and configured
Automation Harness Java/JavaFX agent paths. `preflight.json` verifies the
authoring executable, DISPLAY, D-Bus session, GTK 3, and pyatspi before the GUI
is started.

Snapshots are intentionally non-destructive and are the preferred way for
Codex to preserve evidence immediately after a manually reproduced fault.
