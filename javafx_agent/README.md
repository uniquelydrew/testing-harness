# JavaFX Native Bridge Agent

Linux OpenJFX does not expose the JavaFX scene graph through AT-SPI. This agent
provides the Automation Harness with a loopback-only bridge to the public
JavaFX Node API without requiring changes to application source.

The agent is intentionally compiled without a JavaFX dependency. It uses
reflection at runtime, so one jar can be used with the JavaFX 21 and 22 SDKs.

## Build

Requires JDK 11 or newer. The RHEL target uses JDK 21.

```bash
bash javafx_agent/build.sh
```

`bootstrap.sh` also builds the agent automatically when `javac` and `jar` are
available and exports its path as `AUTOMATION_HARNESS_JAVAFX_AGENT` in
`.automation-harness-env`.

Output:

```text
javafx_agent/build/automation-harness-javafx-agent.jar
```

## Launch a JavaFX application with the bridge

Add the agent before the existing Java arguments:

```bash
java -javaagent:/path/to/automation-harness-javafx-agent.jar ...
```

For the ERSA launch scripts, use a command-scoped `JAVA_TOOL_OPTIONS` value so
the agent is applied only to that JavaFX launcher:

```bash
source .automation-harness-env
cd /home/ersauser/Desktop/git-clones/ersa/core/ops/scripts.example
JAVA_TOOL_OPTIONS="${JAVA_TOOL_OPTIONS:+$JAVA_TOOL_OPTIONS }-javaagent:$AUTOMATION_HARNESS_JAVAFX_AGENT" ./runMVD.csh
```

and, in another terminal if both applications are required:

```bash
source /path/to/testing-harness/.automation-harness-env
cd /home/ersauser/Desktop/git-clones/ersa/core/ops/scripts.example
JAVA_TOOL_OPTIONS="${JAVA_TOOL_OPTIONS:+$JAVA_TOOL_OPTIONS }-javaagent:$AUTOMATION_HARNESS_JAVAFX_AGENT" ./runMosaic.csh
```

Do **not** export this `JAVA_TOOL_OPTIONS` value globally before running the
combined ERSA startup scripts. The current agent requires Java 11+ and must not
be injected into the Java 8 Swing/MSCT process. Swing continues to use Java
Accessibility + java-atk-wrapper/AT-SPI on Linux.

## Qualification

Once an instrumented JavaFX process is running:

```bash
automation-javafx status
```

The command should report one endpoint for each instrumented JVM and list its
JavaFX windows. To prove that the actual scene graph is visible without opening
Object Capture:

```bash
automation-javafx tree --depth 4
```

To test the same click-capture path that Object Capture uses:

```bash
automation-javafx capture --timeout 30
```

Click a control in MVD or Mosaic. The result should include its JavaFX class,
Node `id` when present, accessible role/text, screen bounds, state, hierarchy,
and candidate `javafx` strategy.

`automation-capture` then uses a hybrid capture service: AT-SPI remains active
for native GTK/Swing targets while JavaFX bridge endpoints are queried in
parallel. An AT-SPI failure on JavaFX's empty top-level frame does not terminate
the capture while the JavaFX bridge is still resolving the click.

## Discovery

Each instrumented JVM binds only to the loopback interface on an ephemeral
port and writes a token-protected discovery record under:

```text
/tmp/automation-harness-javafx/javafx-<pid>.json
```

Override the directory with:

```bash
export AUTOMATION_HARNESS_JAVAFX_DISCOVERY_DIR=/some/private/path
```

The record is mode `0600` when POSIX permissions are supported. The directory
is mode `0700`. The bridge removes its discovery record during normal JVM
shutdown; the Python driver also ignores stale/unreachable records.

## Initial protocol surface

Protocol `automation-harness-javafx/1` currently supports:

- `ping`
- `windows`
- `tree`
- `capture_next_click`
- `hit_test`
- `find`
- `state`
- `activate_window`
- `focus`
- `activate` (`fire()` where the JavaFX control exposes it)
- `get_text`
- `set_text`
- `select_menu_path`

Object identity is based on JavaFX properties such as Node `id`, accessible
role/text, control text, native class, parent identity, and window title.
Transient Node references are returned for diagnostics only and are not used as
persisted object identity.

The physical traversal covers JavaFX `Node` / `Parent` scene-graph objects.
Standard menu descendants are also serialized as logical subobjects of their
menu bar/menu owner. `select_menu_path` resolves and activates an entire nested
menu path without returning control between transient submenu transitions.
