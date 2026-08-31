# Optional Automation Harness Java Agent

This Gradle module is the contract boundary for opt-in, in-process Swing and
JavaFX automation.  The Python harness continues to support black-box Java
applications through AT-SPI and Java Access Bridge without this agent.

The agent is launched as a `-javaagent` and exposes a loopback-only JSON service
authenticated by a run-scoped random token. It
returns serialized semantic object snapshots (identity, type, bounds, state,
properties, actions, and logical children) and accepts semantic action
requests. Native `Node` and `Component` instances must never cross the RPC
boundary.

## RPC contract

`GET /health` validates the token and reports framework availability.

`POST /objects/resolve` accepts a compound locator and returns a semantic
snapshot. `POST /objects/action` accepts `{handle, action}` where `action` is
the same serialized `GuiAction` used by Python. The agent performs UI work on
the JavaFX application thread or Swing event-dispatch thread, respectively.

The agent is intentionally scaffolded here because the Python distribution has
no Java build/runtime dependency. Its integration point is the provider
protocol in `automation_harness.models.gui` and the strategy resolver in the
component handle.

## Recording connection

Launch an instrumented target with a token and an unused loopback port:

```text
-javaagent:automation-harness-agent.jar=token=<random>;port=9418
```

The authoring application connects when both environment variables are set:

```text
AUTOMATION_HARNESS_JAVAFX_AGENT_URL=http://127.0.0.1:9418
AUTOMATION_HARNESS_JAVAFX_AGENT_TOKEN=<same random token>
```

For multiple instrumented applications, use comma-separated
`AUTOMATION_HARNESS_JAVAFX_AGENT_URLS` and matching
`AUTOMATION_HARNESS_JAVAFX_AGENT_TOKENS`; transitions are retained as context,
not emitted as test steps.

While `record_start` is active the agent attaches JavaFX scene filters for
pointer release and action events and focused property listeners for text,
selection, and value state. It emits only compact semantic snapshots and
filters movement, skin, layout, CSS, and pressed-state noise at the source.

The same endpoint also supports `capture_next_click` and `hit_test`; both
return `physical_node`, `semantic_node`, and promotion evidence. The target
resolution happens inside the JavaFX process before the response is serialized.
