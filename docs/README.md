# Documentation index

The root [`README.md`](../README.md) is the authoritative user and command
reference. A bundled documentation copy is maintained at
`automation_harness/README.md`; both receive updates for new user-facing commands.

## Current guides

| Document | Scope |
| --- | --- |
| [`live-environment-authoring.md`](live-environment-authoring.md) | Current `live-desktop` authoring model, YAML-backed artifact extensions, regex identity, recording, and click semantics |
| [`script-backed-steps.md`](script-backed-steps.md) | Script-step manifest contract, JSON process protocol, registration, and execution semantics |
| [`javafx-identity-contract.md`](javafx-identity-contract.md) | JavaFX identity, lineage, matching, and bridge requirements |
| [`rhel8-deployment.md`](rhel8-deployment.md) | RHEL 8/Python 3.6 bootstrap, optional pytest setup, and qualification |
| [`pytest-integration.md`](pytest-integration.md) | Experimental `.ahplan` pytest adapter: installation, CLI options, plan discovery, resource loading, JUnit reporting, limits |
| [`../javafx_agent/README.md`](../javafx_agent/README.md) | Loopback JavaFX capture/action bridge |
| [`../java-agent/README.md`](../java-agent/README.md) | HTTP recording-agent contract |

## Historical records

`CHANGELOG_*.md`, `CLEANUP_0.3.1.md`, and `DATAFLOW_0.4.0.md` describe the
state delivered by their named increments. They are retained as historical
records and do not override the current README or guides.

`PLANNED_SURFACE.md` documents intentionally deferred protected-environment
work; it is not a description of implemented public functionality.

## Artifact names

All current authoring artifacts are YAML-backed but use role-specific suffixes:

| Artifact | Preferred suffix |
| --- | --- |
| Authoring project | `.ahproject` |
| Test plan | `.ahplan` |
| Object repository | `.ahobjects` |
| Script-step manifest | `.ahstep` |

Legacy `.yaml` and `.yml` documents remain loadable. New chooser-driven saves
replace those generic suffixes with the appropriate artifact suffix.
