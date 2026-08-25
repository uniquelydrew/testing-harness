# Optional Automation Harness Java Agent

This Gradle module is the contract boundary for opt-in, in-process Swing and
JavaFX automation.  The Python harness continues to support black-box Java
applications through AT-SPI and Java Access Bridge without this agent.

The production agent must be launched as a `-javaagent` and expose a
loopback-only JSON-RPC service authenticated by a run-scoped random token. It
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
