# Automation Harness

Automation Harness is a local-first Linux GUI and systems automation framework built around three reusable assets:

1. **Logical objects** stored in an Object Repository.
2. **Registered steps** with explicit inputs and outputs.
3. **Declarative TestPlans** that connect those objects and steps through test-scoped variables and output bindings.

The framework includes an isolated synthetic reference application so object capture, test composition, state handling, dataflow, and execution can be developed and exercised without access to the eventual target environment.

The current RHEL 8 backport includes the GTK authoring GUI, semantic object
capture, AT-SPI and native JavaFX recording, the Object Identity Workbench,
optional property-level regular expressions, inline plan dependencies,
script-backed steps, declarative TestPlans, and managed execution.

`live-desktop` is the normal execution backend. It operates on the current
desktop session and does not own or select an application; object definitions
carry their own application/window lineage. The synthetic `reference` and
version-pinned `gtk-demo` backends are qualification fixtures. The `protected`
backend remains intentionally disabled.

---

## Contents

- [Mental model](#mental-model)
- [Requirements and installation](#requirements-and-installation)
- [Quick start](#quick-start)
- [Registered steps](#registered-steps)
- [Composing declarative tests](#composing-declarative-tests)
- [Variables, inputs, and outputs](#variables-inputs-and-outputs)
- [Logical objects and the Object Repository](#logical-objects-and-the-object-repository)
- [AT-SPI multi-property identity](#at-spi-multi-property-identity)
- [Referencing objects from tests](#referencing-objects-from-tests)
- [Object state and properties](#object-state-and-properties)
- [Object Capture / Object Spy](#object-capture--object-spy)
- [Object Identity Workbench](#object-identity-workbench)
- [End-to-end recording](#end-to-end-recording)
- [Using the authoring GUI](#using-the-authoring-gui)
- [Managed execution state](#managed-execution-state)
- [Validation and execution](#validation-and-execution)
- [Evidence and run artifacts](#evidence-and-run-artifacts)
- [Python bundle mode](#python-bundle-mode)
- [Command reference](#command-reference)
- [Repository layout](#repository-layout)

---

# Mental model

Automation Harness separates **what an object is**, **what an action does**, and **how a test is composed**.

```text
Object Repository                 Registered Step Catalog
"What objects exist?"             "What reusable actions exist?"
       │                                  │
       └──────────────┬───────────────────┘
                      ▼
                 Declarative TestPlan
                 "What should happen?"
                      │
                      ▼
              Managed Execution Queue
                      │
                      ▼
              Selected Execution Backend
                      │
                      ▼
             Evidence / Execution State
```

A test should not rediscover a GUI object every time it needs to click it, and it should not reimplement an action that already exists.

For example, the logical object:

```text
tracking.follow_button
```

might be stored once with several stable AT-SPI identification conditions. A reusable step such as:

```text
navigation.component.activate
```

can then operate on that object in any TestPlan:

```yaml
- id: follow
  step: navigation.component.activate
  inputs:
    component_id: tracking.follow_button
```

The TestPlan does not know whether the object is found by AT-SPI, another future strategy, or a different implementation detail. It references the logical object ID.

---

# Requirements and installation

## Python

This branch targets the stock **Python 3.6.x** runtime on RHEL 8. The package
metadata intentionally requires `>=3.6,<3.7`.

Python package dependencies are:

- PyYAML 3.12–5.x
- `dataclasses==0.8`
- `typing_extensions==4.1.1`

Pillow is an optional vision dependency. pytest is required only for
development and qualification tests.

Install the wheel into the environment where the harness will run:

```bash
python -m pip install automation_harness-0.5.2-py3-none-any.whl
```

After installation, these commands are available:

```text
automation-run
automation-reference
automation-author
automation-plan
automation-capture
automation-repository
automation-javafx
```

## Linux GUI requirements

The graphical reference target uses GTK when PyGObject and Cairo are available, so its controls are visible to AT-SPI. It falls back to Tk on hosts without the Linux GTK stack; that fallback supports visual testing but not real AT-SPI interaction.

Virtual-display execution uses Xvfb. This is the default for the built-in GUI regression environment because it isolates the synthetic desktop from the user's normal display.

AT-SPI object capture and real accessibility interaction require the host system's `pyatspi` binding. It is intentionally not bundled inside the Python wheel.

On RHEL 8, use `bash bootstrap.sh`; it installs the available native RPMs and
qualifies the complete Python/GTK/AT-SPI combination. See
[`docs/rhel8-deployment.md`](docs/rhel8-deployment.md) for exact behavior.

You can verify the installation with:

```bash
automation-run selftest
```

On a host intended to qualify real AT-SPI interaction:

```bash
automation-run selftest --require-atspi
```

The second form fails qualification if the host cannot run the real AT-SPI integration test.
When it is launched outside a desktop session, the harness creates an isolated D-Bus session automatically with `dbus-run-session`.

## Java Swing and JavaFX targets

Java applications are external to the backend lifecycle. Launch them before a
test, or launch them from a contract-backed script step in the plan. Swing uses
the Java accessibility bridge through AT-SPI on Linux. JavaFX should be
launched with the native bridge agent so capture, recording, resolution, and
actions use its semantic scene graph.

On Windows, enable the x64 Java Access Bridge provided with the JDK. On
Linux/X11, install the Java ATK wrapper and enable it in the external Swing
launch command. Custom Swing components and JavaFX nodes must still expose a
meaningful accessible name and role. Some Linux JavaFX runtimes expose an
embedded `JFXPanel` as one accessible panel rather than exposing its child
controls. Object Capture handles that case with a read-only `anchored_visual`
strategy: it segments the clicked visual region and stores normalized bounds
relative to the durable accessible panel anchor.

Use `java_accessibility` component strategies for application controls. They use Java Access Bridge on Windows and AT-SPI through the Java ATK wrapper on Linux. For visuals, resolve a stable canvas/panel, then use `ctx.component("logical.id").assert_visual()` for an approved component-bound gold, or `vision.wait_for_color` for a lightweight color check. Approved PNGs and optional grayscale masks live under the repository's `visual/` directory; the exact host visual profile selects the variant. Stage and review a new candidate with `automation-run visual stage`, then promote it explicitly with `automation-run visual approve`. Baseline comparison stores expected, actual, and diff images; a black pixel in an optional grayscale mask ignores volatile regions.

The packaged `automation_harness/examples/java_desktop` directory is retained
as a legacy visual-bundle template. Current declarative authoring uses a
self-contained `.ahplan` and `live-desktop`; application startup belongs in a
script-backed plan step when required.

---

# Quick start

A typical authoring workflow is:

```text
1. Create or open an authoring project.
2. Launch the target application, or add a script-backed setup step.
3. Capture a control and save its stable logical identity.
4. Select that object to see only actions it supports.
5. Configure an action through typed fields and add it to Test Flow.
6. Add synchronization and assertions from the same object-scoped Actions view.
7. Validate and run the test against the configured project target.
8. Review live node state and the generated run artifacts.
9. Optionally save a proven composition as a reusable step.
```

Start the GUI and create a project from **New Project**:

```bash
automation-author
```

Or open an existing authoring project directly:

```bash
automation-author --project ./project.ahproject
```

An authoring project connects its object repository, run artifacts, and
optional script-step implementations:

```yaml
version: 1
name: Login workflow
repository: objects.ahobjects
runs_dir: runs
script_steps:
  - script_steps/prepare-environment.ahstep
```

Inside the GUI, capture an object, select it in **Object Repository**, choose
one of its supported **Actions**, and click **Add Action to Test**. The object
binding is preserved automatically. Add **Wait for State** and **Assert State**
the same way, then use **Run Test**.

The application may already be running, or the first plan step may launch it.
There is no test-level application selector: a plan can interact with multiple
applications because each object owns its own locator lineage.

The same plan can be executed from the CLI:

```bash
automation-plan ./test.ahplan --backend live-desktop
```

When a plan intentionally uses the synthetic qualification fixture, select it
explicitly with `--backend reference`.

The older repository-only entry point remains available for migration and
low-level repository editing:

```bash
automation-author --repository ./objects.ahobjects
```

Or launch it without one and choose a repository when saving the first captured object:

```bash
automation-author
```

Inspect the internal execution catalog for diagnostics:

```bash
automation-run steps list
```

Inspect one step's exact input and output contract:

```bash
automation-run steps describe navigation.component.activate
```

Validate a declarative plan:

```bash
automation-run plan validate ./test.ahplan --components ./objects.ahobjects
```

Inspect its initial queue state:

```bash
automation-run plan status ./test.ahplan
```

Run it against the reference target:

```bash
automation-run plan run ./test.ahplan \
  --components ./objects.ahobjects \
  --backend reference
```

---

# Registered steps

A **registered step** is a reusable semantic action or assertion with a stable ID.

Examples currently installed include:

```text
navigation.component.resolve
navigation.component.activate
component.state.get
component.state.wait
component.state.assert
component.property.get
track.create_moving
track.wait_for_motion
track.follow
validation.equal
```

Use:

```bash
automation-run steps list
```

to list the complete current catalog.

Filter by domain:

```bash
automation-run steps list --domain component
```

For machine-readable metadata:

```bash
automation-run steps list --json
```

## Step contracts

Every registered step exposes:

- stable step ID
- description
- domain
- input names
- input types
- required/default status
- output names
- output selectors
- required backend capabilities
- risk classification
- implementation digest

For example:

```bash
automation-run steps describe component.state.wait
```

reports a contract equivalent to:

```text
Inputs:
  component_id   str      required
  state_name     str      required
  expected       Any      required
  timeout        float    default 5.0
  interval       float    default 0.1

Outputs:
  state          entire returned ComponentState
```

This metadata is an internal execution contract. The authoring GUI instead derives
contextual Actions from the selected captured object and adds them to Test Flow.

## Do not duplicate an existing step

The intended workflow is reusable-action first:

```text
Need behavior
    │
    ├─ Step already exists → reuse the registered ID
    │
    └─ Step genuinely new → implement/register it once, then reuse it
```

TestPlans should normally compose installed reusable steps rather than duplicate their implementation.

---

# Composing declarative tests

The preferred test-composition format is a YAML **TestPlan**.

A TestPlan has four primary pieces:

```yaml
name: example
version: 1
variables: {}
steps: []
```

## Minimal test

```yaml
name: resolve-object
version: 1
steps:
  - id: resolve-follow
    step: navigation.component.resolve
    inputs:
      component_id: tracking.follow_button
```

Each item in `steps` is one `StepCall`.

### `id`

The node ID is unique within the plan:

```yaml
id: resolve-follow
```

If omitted when plans are loaded programmatically, the loader can generate `step-001`, `step-002`, etc., but explicit descriptive IDs are recommended.

### `step`

The stable registered-step ID:

```yaml
step: navigation.component.resolve
```

### `description`

Optional human-readable description:

```yaml
description: Resolve the Follow button before use.
```

### `inputs`

Named inputs supplied to the registered step:

```yaml
inputs:
  component_id: tracking.follow_button
```

Literal values are validated against known step input types where the harness can determine them statically.

### `outputs`

Routes a declared step output into a test-global variable:

```yaml
outputs:
  strategy: follow_resolution_strategy
```

The left side is the **registered step output name**. The right side is the **global variable to create/update**.

### `depends_on`

Explicit ordering dependency:

```yaml
depends_on:
  - open-menu
```

Use `depends_on` when sequencing is required even though no output variable naturally creates a data dependency.

Do not use it merely to reproduce the written order of the YAML file. The managed execution engine derives readiness from actual data and dependency requirements.

---

# Variables, inputs, and outputs

Test-global variables provide the UFT-style dataflow between reusable steps.

## Initialize globals in the plan

```yaml
name: follow-track
version: 1
variables:
  requested_track: alpha
  retry_count: 0
  session:
    phase: initialized
  history: []
```

Top-level variable names must not contain dots.

Dots are reserved for **references into nested data**.

## Reference a variable

The canonical YAML form is:

```yaml
inputs:
  track_id:
    $var: active_track
```

String shorthand is also accepted by the plan loader:

```yaml
inputs:
  track_id: ${active_track}
```

Nested references use dot notation:

```yaml
inputs:
  value:
    $var: session.phase
```

List indexes can also be traversed:

```text
history.0.track_id
```

## Bind one step's outputs

Given a step that declares outputs:

```text
track_id
x
y
visible
followed
```

a plan can bind only the values it needs:

```yaml
- id: create-track
  step: track.create_moving
  inputs:
    track_id: alpha
    x: 10.0
    y: 20.0
  outputs:
    track_id: active_track
    x: initial_x
```

This creates or updates:

```text
active_track
initial_x
```

The output variable itself is a top-level test-global variable. Nested output targets such as `session.track_id` are intentionally not accepted by the declarative output-binding syntax.

## Feed outputs into later inputs

```yaml
- id: wait-for-motion
  step: track.wait_for_motion
  inputs:
    track_id:
      $var: active_track
    initial_x:
      $var: initial_x
  outputs:
    x: moved_x
```

The consumer is blocked until the required variables exist.

The producer does not need to appear textually before the consumer for the graph to be valid. If the variable has a unique producer, the managed queue can hold the consumer in `BLOCKED` state until that producer commits.

## Full example

```yaml
name: reference-track-follow-plan
version: 1

variables:
  requested_track: plan-alpha

steps:
  - id: create-track
    step: track.create_moving
    inputs:
      track_id:
        $var: requested_track
      x: 10.0
      y: 20.0
      vx: 8.0
      vy: 1.0
    outputs:
      track_id: active_track
      x: initial_x

  - id: wait-for-motion
    step: track.wait_for_motion
    inputs:
      track_id:
        $var: active_track
      initial_x:
        $var: initial_x
    outputs:
      x: moved_x

  - id: follow-track
    step: track.follow
    inputs:
      track_id:
        $var: active_track
    outputs:
      followed: followed

  - id: validate-followed
    step: validation.equal
    inputs:
      name: followed
      actual:
        $var: followed
      expected: true
```

## Runtime variable overrides

A plan default can be initialized or overridden when running:

```bash
automation-run plan run test.ahplan \
  --backend reference \
  --var requested_track='"bravo"' \
  --var retry_count=3
```

`VALUE` is interpreted as JSON when possible. For example:

```text
3               → integer
true            → boolean
[1,2,3]         → list
{"x":10}        → mapping
"bravo"         → JSON string
```

If a value is not valid JSON, it is treated as a string by the CLI variable parser.

---

# Logical objects and the Object Repository

Tests should reference GUI objects by stable **logical component ID**, not by coordinates, ad hoc accessibility searches, or test-specific screenshots.

A repository is a YAML document:

```yaml
version: 1
components:
  tracking.follow_button:
    description: Follow the selected track.
    revision: 1
    actions:
      - resolve
      - activate
    strategies:
      - type: atspi
        identification:
          mandatory:
            accessible_id: followButton
            role: push button
          assistive:
            name: Follow
            application: Reference Application
            window: Tracking Window
            parent:
              name: Tracking
              role: tool bar
```

## Component ID

The key under `components` is the reusable logical name:

```text
tracking.follow_button
```

Choose IDs according to the object's semantic role rather than its screen coordinates or current hierarchy position.

Good:

```text
tracking.follow_button
navigation.file_menu
mosaic.add_tile_button
session.username_field
```

Poor:

```text
button_4
x1182_y744
third_button
```

## Description

```yaml
description: Follow the selected track.
```

Use this to document why the object exists or how it is expected to be used.

## Revision

Captured repository objects are revisioned:

```yaml
revision: 3
```

Saving a recapture over an existing logical component increments its revision.

## Actions

Actions declare what the logical component supports:

```yaml
actions:
  - resolve
  - activate
```

A component that is intentionally inspection-only may support only:

```yaml
actions:
  - resolve
```

The harness does not assume that every resolvable object is activatable.

## Standard menu hierarchies

Captured AT-SPI and JavaFX menu bars persist standard menus, submenus, and
items as nested `subobjects`. The authoring UI presents each terminal path as
one **Select Menu Item** action:

```yaml
components:
  application.menu_bar:
    actions: [resolve, select_menu_item]
    subobjects:
      file:
        kind: menu
        criteria: {name: File, role: menu}
        subobjects:
          recent:
            kind: menu
            criteria: {name: Recent, role: menu}
            subobjects:
              report:
                kind: menu_item
                criteria: {name: Report, role: menu item}
```

A test stores only the stable subobject IDs:

```yaml
- id: open-recent-report
  step: gui.object.action
  inputs:
    component_id: application.menu_bar
    action:
      type: select_menu_item
      path: [file, recent, report]
```

The backend resolves and activates the entire path in one call. It does not
return control between menu-opening operations, so transient submenus remain
open until the terminal item is activated.

## Strategies

`strategies` contains ordered mechanisms for locating/observing the object.

Current desktop identity uses AT-SPI for GTK/Swing controls and the native
JavaFX bridge for instrumented JavaFX applications. The synthetic reference
repository also uses `reference_inspection` for read-only qualification state;
that strategy is not a replacement for real UI interaction.

---

# AT-SPI multi-property identity

An AT-SPI locator is intentionally richer than a single name or role.

The identification model has:

1. **Mandatory conditions**
2. **Assistive conditions**
3. Optional explicit **ordinal**

## Mandatory conditions

Mandatory properties are always applied and are conjunctive:

```yaml
mandatory:
  accessible_id: followButton
  role: push button
```

This means:

```text
accessible_id == followButton
AND
role == push button
```

It does **not** mean "match either one."

## Assistive conditions

Assistive conditions are used progressively only while the mandatory set still leaves multiple runtime candidates:

```yaml
assistive:
  name: Follow
  application: Reference Application
  window: Tracking Window
  parent:
    name: Tracking
    role: tool bar
```

Resolution behaves conceptually as:

```text
Apply every mandatory condition
        │
        ├─ 0 candidates → not found
        ├─ 1 candidate  → resolved
        └─ 2+ candidates
                │
                ▼
        add assistive #1
                │
                ├─ 1 candidate → resolved
                └─ 2+ candidates
                        │
                        ▼
                add assistive #2
                        │
                        ...
```

Assistive property order is therefore meaningful.

The goal is not to minimize the number of properties stored. The repository should retain several stable facts about the object so it can disambiguate if the surrounding UI changes.

## Explicit ordinal

If multiple genuinely equivalent objects remain after stable identification properties are exhausted, an author may supply a zero-based ordinal:

```yaml
identification:
  mandatory:
    role: push button
    name: Delete
  assistive:
    window: Item List
  ordinal: 1
```

Ordinal is a last-resort authored discriminator. The capture mechanism never invents one automatically, and runtime tree order is never silently used as a substitute.

## Supported AT-SPI identification properties

The current repository schema supports these top-level properties:

```text
name
role
accessible_id
application
window
hierarchy
parent
attribute:<attribute-name>
```

### `parent`

Parent identity can include:

```yaml
parent:
  name: Tracking
  role: tool bar
  accessible_id: trackingToolbar
```

### `hierarchy`

Hierarchy is an ordered list of ancestor descriptors captured from the accessible tree:

```yaml
hierarchy:
  - Reference Application
  - Tracking Window
  - Tracking Toolbar
```

### Backend attributes

A backend accessibility attribute can be matched explicitly:

```yaml
attribute:automation-id: some-value
```

Use backend attributes only when they are stable application identifiers, not volatile runtime data.

## Identity versus state

Transient state is **not identity**.

For example, if a button is disabled when captured, `enabled: false` should not normally become part of the locator. The object is still the same object after it becomes enabled.

Similarly, screen geometry is recorded as observation/evidence, not durable identity.

---

# Referencing objects from tests

Once a repository object exists, tests refer to its logical ID through component-oriented registered steps.

## Resolve an object

```yaml
- id: resolve-follow
  step: navigation.component.resolve
  inputs:
    component_id: tracking.follow_button
  outputs:
    strategy: resolution_strategy
```

`navigation.component.resolve` exposes:

```text
component
component_id
strategy
```

as named outputs.

## Activate an object

```yaml
- id: click-follow
  step: navigation.component.activate
  inputs:
    component_id: tracking.follow_button
```

Activation resolves the component through its configured strategy and performs only an explicitly supported activation action. The AT-SPI driver does not fall back to invoking an arbitrary first accessibility action.

## Validate literal object references before execution

When a plan contains a literal component ID, validate it against the same repository that will be supplied at runtime:

```bash
automation-run plan validate ./test.ahplan \
  --components ./objects.ahobjects
```

Unknown literal IDs are reported before the reference backend is started, including close-match suggestions when available.

If a plan attempts `navigation.component.activate` on an object whose repository definition does not include `activate`, validation rejects that use.

---

# Object state and properties

Component state is observed independently of object identity.

First-class state fields currently include:

```text
present
visible
showing
enabled
focused
selected
checked
pressed
expanded
editable
readonly
active
sensitive
```

A value of `null` / Python `None` means the current backend does not expose that state. It is distinct from `false`.

## Read the entire state

```yaml
- id: read-follow-state
  step: component.state.get
  inputs:
    component_id: tracking.follow_button
  outputs:
    enabled: follow_enabled
    visible: follow_visible
```

`component.state.get` exposes:

```text
state
present
visible
enabled
expanded
```

as named outputs.

## Read one property/state value

```yaml
- id: read-button-name
  step: component.property.get
  inputs:
    component_id: tracking.follow_button
    property_name: name
  outputs:
    value: follow_name
```

## Assert state

```yaml
- id: assert-disabled
  step: component.state.assert
  inputs:
    component_id: tracking.follow_button
    state_name: enabled
    expected: false
```

## Wait for state

```yaml
- id: wait-until-enabled
  step: component.state.wait
  inputs:
    component_id: tracking.follow_button
    state_name: enabled
    expected: true
    timeout: 10.0
    interval: 0.1
```

The wait observes the actual object repeatedly rather than assuming that a preceding action caused the desired state.

This is the recommended pattern for asynchronous UI behavior.

### Menu-open example

A menu may expose `expanded` directly:

```yaml
- id: wait-menu-open
  step: component.state.wait
  inputs:
    component_id: navigation.file_menu
    state_name: expanded
    expected: true
```

If the toolkit does not expose `expanded`, capture the menu popup itself and wait for its `present` or `visible` state instead:

```yaml
- id: wait-popup
  step: component.state.wait
  inputs:
    component_id: navigation.file_menu_popup
    state_name: present
    expected: true
```

AT-SPI absence is represented as `present: false`, allowing disappearance waits such as closing dialogs or menus.

---

# Object Capture / Object Spy

# GTK 4.14 Demo baseline

The GTK Demo baseline is a Linux-only, real-AT-SPI qualification target. It is pinned to **GTK 4.14.x**: an intentional minor-version upgrade requires recapturing the component repositories and reviewing the expected accessibility tree.

Install GTK Demo, `pyatspi`, Xvfb, and run within a desktop or `dbus-run-session` environment. The executable is resolved from `--gtk-demo-executable`, `AUTOMATION_HARNESS_GTK_DEMO`, or `gtk4-demo`.

Run the complete catalog:

```bash
automation-run gtk-demo selftest
```

Run an individual bundle:

```bash
automation-run run automation_harness/examples/gtk4_demo/buttons --backend gtk-demo
```

Each example starts in a fresh process and has its own object repository. Use `automation-capture` against the upgraded target to replace its semantic AT-SPI locators; avoid geometry and ordinals unless no stable accessible identity exists.

Object Capture is the mechanism for converting a live Linux desktop accessibility object into a reusable logical repository object.

The capture service is available through the local authoring GUI.

For focused tools, launch Object Capture or the Object Repository editor independently:

```bash
automation-capture --repository ./objects.ahobjects
automation-repository --repository ./objects.ahobjects
```

The repository launcher lets you inspect and edit the selected component as JSON; it validates the definition before saving it back to the supplied YAML repository.

## Launch with an editable repository

```bash
automation-author --repository ./objects.ahobjects
```

The Object Repository panel shows the current logical objects. The capture tools inspect live AT-SPI objects and can save them into the selected repository.

If `pyatspi` is not installed, capture controls report AT-SPI as unavailable rather than fabricating a synthetic UI interaction path.

## Capture methods

There are three supported capture workflows.

### 1. Capture next click

Use **Capture Next Click** for the normal object-spy workflow. The authoring
window withdraws and a nearly transparent desktop picker owns exactly one full
mouse click. After release, the picker closes and resolution proceeds in one
scoped operation: first the application at the desktop coordinate, then the
deepest component inside that application. The picker consumes both press and
release, so capture does not activate or mutate the inspected control.

The captured bounds are outlined in red before the naming prompt opens. Use
**Highlight Last Capture** to repeat that check. Every repository row also has
a right-click **Highlight** command; it retries live resolution for five
seconds and reports an error when no match is found.

When a bridge exposes only a generic panel/canvas, Object Capture segments the
visual region under the click and authors a read-only `anchored_visual`
strategy. It resolves the accessible container at runtime, scales the stored
relative bounds to its current size, and supports the same capture and
repository highlight checks.

Capture Next Click resolves one uninterrupted click only. For menu bars and
other interactions that require a sequence of transient UI states, use
recording or capture the menu bar itself. Standard menu descendants are queried
while visible and stored as logical `subobjects`; **Select Menu Item** then
executes the complete menu/submenu/item path atomically.

### 2. Capture by pointer

Use this when you can point at the object visually.

Conceptually:

```text
Capture mode
    ↓
choose screen point/object
    ↓
inspect accessible object at that position
    ↓
collect hierarchy, identity, actions, state, bounds
    ↓
assess locator stages
    ↓
review/edit identity
    ↓
save logical component
```

The capture result includes information such as:

```text
accessible name
role
description
accessible ID
application
window
parent name/role/ID
ancestor hierarchy
actions
bounds
current state
backend properties
candidate locator strategy
```

### 3. Capture by locator

Use **Capture by locator** when you already know some accessibility properties.

The GUI prompts for:

```text
Accessible name
Role
Accessible ID
```

Blank fields are allowed, but at least one must be supplied.

This initial locator finds the live object for inspection; it is not necessarily the final repository identity.

## Candidate identification

After capture, the harness creates a candidate multi-property identity.

If an accessible ID exists, the candidate normally favors it as a strong mandatory condition, typically alongside role:

```yaml
mandatory:
  accessible_id: followButton
  role: push button
```

Stable contextual properties are retained as assistive conditions:

```yaml
assistive:
  name: Follow
  application: Reference Application
  window: Tracking Window
  parent:
    name: Tracking
    role: tool bar
```

If no accessible ID exists, name and role become the usual mandatory pair when available.

Geometry and indexes are deliberately excluded from automatically generated durable identity.

## Locator assessment

Object Capture evaluates the candidate identity against the live accessibility tree.

For each stage it records:

```text
source/stage
criteria applied
number of runtime matches
whether the result is unique
stability assessment
```

The authoring GUI presents the capture and locator-assessment data before persistence.

A conceptual assessment might look like:

```text
mandatory: role=push button, name=Follow          3 matches
+ accessible_id=followButton                     1 match
```

or:

```text
mandatory: accessible_id=itemAction, role=button 4 matches
+ window=Item Details                            2 matches
+ parent.name=Action Bar                         1 match
```

The framework does not consider "several match conditions" an error. Several conditions are desirable. An error occurs only when **multiple runtime objects still satisfy the complete authored identity** and no explicit ordinal has been supplied.

## Editing the captured identity

Before saving, the GUI presents the identity as JSON so the author can change mandatory/assistive classification or add an ordinal.

Example:

```json
{
  "mandatory": {
    "accessible_id": "followButton",
    "role": "push button"
  },
  "assistive": {
    "name": "Follow",
    "window": "Tracking Window",
    "parent": {
      "name": "Tracking",
      "role": "tool bar"
    }
  }
}
```

The saved YAML repository preserves that structure.

## Ambiguous capture

If the authored identity still resolves multiple runtime objects, capture refuses to save it unless an explicit ordinal resolves the remaining candidates.

The preferred remediation order is:

```text
1. Add a stable accessible ID if available.
2. Add semantic name/role.
3. Add application/window scope.
4. Add stable parent conditions.
5. Add hierarchy or stable backend attributes where appropriate.
6. Use ordinal only when equivalent objects genuinely cannot be distinguished semantically.
```

Do not use current screen position as identity merely to force uniqueness.

## Recapture and revisions

If a logical component already exists and is captured again under the same ID, the repository increments its `revision`.

That supports maintenance without forcing every test to change its component reference.

The tests continue to refer to:

```text
tracking.follow_button
```

while its repository identity can evolve from revision 1 to revision 2, 3, etc.

## Capture best practices

Prefer properties in roughly this order when they are stable:

```text
accessible_id     strongest when application-provided and stable
role              strong semantic object class
name              strong when not dynamic/localized unpredictably
application       useful scope
window            useful scope
parent identity   useful structural scope
hierarchy         useful but more sensitive to UI restructuring
ordinal           explicit last resort
geometry          observation only, not durable identity
```

Do not blindly include every property. Avoid transient text, changing counts, timestamps, current values, selection state, enabled state, and screen coordinates as identity unless the application specifically guarantees them as durable identifiers.

## Object Identity Workbench

Every capture opens the Object Identity Workbench before persistence. The
workbench applies the same semantic-boundary resolver used by recording:

- implementation-only JavaFX skins, labels, and layout nodes are collapsed
  under the nearest actionable control;
- stable application-authored controls remain eligible semantic boundaries;
- standard menus expose nested logical subobjects;
- structural ancestry can contribute identity without becoming saveable;
- selected identity properties can use Exact or Regex matching; and
- ambiguous AT-SPI identities must be refined or assigned an explicit ordinal.

Saving to an existing repository updates the selected logical object and
increments its revision. Saving to a new repository creates a normal
`.ahobjects` document.

## End-to-end recording

Use **Start Recording**, interact with any live applications, and then use the
floating **Stop Recording** control. Stop processing runs off the GTK thread;
the authoring window is restored while adapters finish and the recording is
correlated.

Recording and Capture Next Click use the same semantic target policy. On X11,
the physical pointer monitor first resolves the topmost client window and its
owning process. Only a JavaFX bridge belonging to that process may hit-test the
point. Swing then falls back to java-atk-wrapper/AT-SPI, and that accessibility
snapshot must report the same process owner. This prevents covered windows from
capturing a click based only on overlapping screen bounds. Passive labels,
generic panels, authoring chrome, focus transitions, and other presentation
noise are not emitted as test actions.

After recording stops, the Object Identity Workbench opens automatically with
every distinct interacted object checked. Its review tree is intentionally
compact:

```text
Recorded interaction scope
  Window A
    checked semantic object
    checked semantic object
  Window B
    checked semantic object
```

Raw accessibility ancestry is retained in capture evidence but is not rendered
as saveable object rows. Repeated interactions with one durable object produce
one checked target. Review and save those objects before adding recorded
interactions to Test Flow; an interaction must have a unique repository match
before it can become a plan step.

---

# Using the authoring GUI

Launch:

```bash
automation-author --repository ./objects.ahobjects
```

The GUI currently exposes the same underlying repositories, Step Registry, and TestPlan model used by the CLI.

It does **not** have a separate execution implementation.

## Object Repository

Use the Object Repository view to:

- open an `.ahobjects` repository (legacy `.yaml`/`.yml` remains readable)
- browse captured logical IDs
- inspect definitions
- capture an object
- save/recapture an object
- inspect revision and locator details

## Actions

The Actions view is scoped to the selected captured object. It lists only
interactions supported by that object's semantic type and repository metadata,
plus applicable observation, synchronization, and assertion actions.

Selecting a step shows its metadata, including:

```text
inputs
outputs
capabilities
risk
description
aliases
implementation digest
```

Use **Add Action to Test** to configure the action and append it to Test Flow.
The selected object is bound automatically. Atomic framework executors are not
presented as user-authored reusable content.

## Test Flow

Each TestPlan row corresponds to one registered step call.

The recording table is a staging area, not a second plan representation. Use
**Keep** or **Delete** to curate observations, save/refine new targets in the
Object Identity Workbench, then use **Add Selected as Step**. Recorded actions
are added to the currently selected step group, preserving the same conceptual
grouping used by manually composed actions.

New actions use schema-generated input fields. The advanced plan editor still
accepts JSON for migration and low-level editing. Variable references use:

```json
{"$var":"active_track"}
```

For example:

```json
{
  "track_id": {"$var":"active_track"},
  "initial_x": {"$var":"initial_x"}
}
```

Output bindings are a separate JSON mapping:

```json
{
  "x": "moved_x"
}
```

This means:

```text
step output x → global variable moved_x
```

The GUI serializes these bindings into the same YAML TestPlan format accepted by `automation-run plan ...`.

## Variables

The Variables view edits the TestPlan's initial global-variable mapping.

Example:

```json
{
  "requested_track": "alpha",
  "session": {
    "phase": "initialized"
  },
  "history": []
}
```

## Managed queue projection

The GUI shows the initial state of each node:

```text
READY
BLOCKED
PENDING
```

and unresolved variable references where relevant.

After a reference run, the GUI can load the resulting `execution_state.json` and display the final node states.

## Run Test

The GUI's **Run Test** action:

1. validates the current plan
2. validates component references against the currently opened repository
3. starts the target configured by the authoring project
4. runs the same declarative execution engine used by the CLI
5. writes normal run artifacts
6. reloads final execution state into the GUI

This is intentionally the same execution path as:

```bash
automation-run plan run ... --backend reference
```

---

# Managed execution state

A TestPlan is immutable test composition. Each run creates mutable `ExecutionState` around it.

Each step node can be:

```text
PENDING
BLOCKED
READY
RUNNING
PASSED
FAILED
SKIPPED
CANCELLED
```

## Data dependency

Consider:

```yaml
- id: follow
  step: track.follow
  inputs:
    track_id:
      $var: active_track
```

If `active_track` does not yet exist, the node is `BLOCKED`.

After another step commits:

```text
active_track = alpha
```

the scheduler recomputes readiness and can transition `follow` to `READY`.

## Explicit dependency

```yaml
- id: verify-dialog
  step: component.state.assert
  depends_on:
    - open-dialog
  inputs:
    component_id: dialogs.details
    state_name: present
    expected: true
```

Even if all inputs already exist, `verify-dialog` remains blocked until `open-dialog` passes.

## Queue state is derived

The queue is not itself the source of truth. It is derived from:

```text
immutable TestPlan
+
committed global variables
+
per-node execution status
```

That allows queue readiness to be recomputed after each successful output transaction.

---

# Validation and execution

## Validate a plan

```bash
automation-run plan validate ./test.ahplan
```

With an external object repository:

```bash
automation-run plan validate ./test.ahplan \
  --components ./objects.ahobjects
```

Reference-backend-specific preflight can also be requested:

```bash
automation-run plan validate ./test.ahplan \
  --components ./objects.ahobjects \
  --backend reference
```

Validation checks include, where statically knowable:

- unique node IDs
- registered step IDs
- valid input names
- required inputs
- basic literal input types
- valid output names
- valid output variable names
- dependency references
- variable producers/references
- dependency/dataflow cycles
- literal component IDs
- component activation capability
- backend capabilities when a backend is supplied
- backend step-risk authorization when a backend is supplied

## Inspect initial status

```bash
automation-run plan status ./test.ahplan
```

Machine-readable form:

```bash
automation-run plan status ./test.ahplan --json
```

Example output:

```text
create-track       track.create_moving               ready    waiting=-
wait-for-motion    track.wait_for_motion              blocked  waiting=active_track,initial_x
follow-track       track.follow                       blocked  waiting=active_track
```

## Execute

```bash
automation-run plan run ./test.ahplan \
  --backend reference \
  --components ./objects.ahobjects \
  --runs-dir ./runs
```

Reference GUI mode is the default.

Use the isolated virtual display explicitly:

```bash
automation-run plan run ./test.ahplan \
  --backend reference \
  --reference-mode gui \
  --reference-display virtual
```

For service-only reference scenarios:

```bash
automation-run plan run ./test.ahplan \
  --backend reference \
  --reference-mode headless
```

---

# Evidence and run artifacts

Each run receives a timestamped artifact directory under the selected `--runs-dir`.

A declarative run contains files including:

```text
<run>/
  events.jsonl
  execution_state.json
  registered_steps.json
  run.json
  environment.json
  summary.txt
  logs/
```

The generic artifact model also reserves paths for:

```text
junit.xml
stdout.log
stderr.log
```

which are particularly relevant to pytest/bundle execution.

## `events.jsonl`

Structured execution evidence is written as JSON Lines.

Events include information such as:

```text
plan_run_started
plan_qualified
backend_health
plan_step_started
step_started
variable_resolved
variable_set
variable_transaction_committed
step_finished
plan_step_finished
plan_globals_final
plan_run_finished
```

Component resolution and state operations also record their own evidence.

## `execution_state.json`

Contains the managed execution graph's current/final state:

```text
plan name
current globals
per-node status
resolved inputs
committed outputs
errors
attempt count
unresolved variables
```

The file is rewritten after state transitions so it can be inspected during or after execution.

## `registered_steps.json`

Records the exact installed step catalog used for the run, including implementation SHA-256 values.

## `environment.json`

Records execution metadata including:

```text
execution mode
backend
capabilities
initial globals
plan hash
step catalog hash
platform
```

---

# Python bundle mode

Declarative TestPlans are the primary composition model for tests built from installed reusable steps.

A separate Python/pytest bundle mode remains available for framework development, qualification, and reusable-step development.

Example bundle manifest:

```yaml
name: reference-regression-suite
version: 1

step_libraries:
  - steps/reference_workflows.py

variables:
  history: []
  session:
    phase: initialized

requires:
  - reference
  - tracking
  - mosaic
  - threat-state
  - triangulation

tests:
  - tests/test_track_follow.py
```

The Python bundle path can associate reusable step-library files and use `TestContext` directly.

For example:

```python
from automation_harness.core.step_registry import step


@step(
    "workflow.raise_threat_and_verify",
    domain="workflow",
    capabilities={"threat-state"},
    outputs={"level": "$"},
)
def raise_threat_and_verify(ctx, level: str) -> str:
    expected = level.upper()
    ctx.run_step("threat.level.set", expected)
    actual = ctx.run_step("threat.level.get")
    ctx.run_step("validation.equal", "workflow_threat_level", actual, expected)
    return actual
```

A Python test can invoke a registered step and route outputs:

```python
ctx.run_step(
    "track.create_moving",
    "alpha",
    bind_outputs={
        "track_id": "active_track",
        "x": "initial_x",
    },
)

ctx.run_step(
    "track.wait_for_motion",
    ctx.ref("active_track"),
    initial_x=ctx.ref("initial_x"),
    bind_outputs={"x": "moved_x"},
)
```

Python bundle globals also support explicit mutation operations:

```python
ctx.globals.set("attempt", 1)
ctx.globals.update("session", {"phase": "tracking"})
ctx.globals.append("history", {"event": "track-created"})
ctx.globals.extend("history", [{"event": "followed"}, {"event": "verified"}])
```

These operations are useful when implementing reusable workflows, but a normal declarative TestPlan should prefer explicit step inputs/outputs so the dataflow remains visible in the composition.

Run a Python bundle with:

```bash
automation-run validate ./bundle

automation-run inspect ./bundle

automation-run run ./bundle --backend reference
```

---

# Command reference

## Qualification

```bash
automation-run selftest
```

Require real AT-SPI qualification:

```bash
automation-run selftest --require-atspi
```

Choose reference display policy:

```bash
automation-run selftest --reference-display virtual
```

## Step Registry

```bash
automation-run steps list

automation-run steps list --domain track

automation-run steps list --json

automation-run steps describe track.follow

automation-run steps describe track.follow --json
```

## Declarative plans

```bash
automation-run plan validate ./test.ahplan

automation-run plan validate ./test.ahplan --components ./objects.ahobjects

automation-run plan validate ./test.ahplan --backend reference

automation-run plan status ./test.ahplan

automation-run plan status ./test.ahplan --json

automation-run plan run ./test.ahplan --backend reference

automation-run plan run ./test.ahplan --backend reference --components ./objects.ahobjects

automation-run plan run ./test.ahplan --backend reference --var track_id='"alpha"'
```

## Python bundles

```bash
automation-run validate ./bundle

automation-run inspect ./bundle

automation-run run ./bundle --backend reference
```

## Authoring GUI

```bash
automation-author

automation-author --repository ./objects.ahobjects
```

GUI construction smoke test:

```bash
automation-author --smoke-test
```

## Reference application

```bash
automation-reference
```

Normal reference execution is generally started through `automation-run` so backend lifecycle, evidence, and environment setup remain coordinated.

---

# Repository layout

Key source areas:

```text
automation_harness/
  authoring/
    app.py                    local Object Capture / Actions / Test Flow GUI
    capture_context.py        semantic workbench trees and recording review scope
    object_identity_workbench.py identity-property review and persistence

  backends/
    base.py                   execution-backend contract
    live_desktop.py           current desktop execution facility
    reference.py              synthetic reference backend
    protected.py              intentionally disabled placeholder boundary

  core/
    component_handle.py       resolve/state/activate logical object facade
    component_repository.py   object-repository loading, overlay, validation
    object_capture.py         Object Spy capture and repository persistence
    services.py               backend-neutral semantic service contracts
    step_registry.py          reusable-step registration and I/O transactions
    test_context.py           runtime context supplied to reusable steps/tests
    test_plan.py              YAML plan loading, validation, queue/state model
    variables.py              test-global variable store and references

  drivers/
    atspi_driver.py           Linux accessibility discovery/interaction/capture
    javafx_bridge.py          native JavaFX bridge discovery and protocol client
    tracking_driver.py        tracking-facing driver abstraction
    vision_driver.py          framebuffer/vision primitives

  models/
    component.py              component, state, capture, identity models
    plan.py                   TestPlan and ExecutionState models

  recording/
    adapters/                 AT-SPI and JavaFX event sources
    session.py                correlation, repository matching, plan conversion

  reference/
    gui.py                    synthetic desktop reference application
    state.py                  synthetic service/state model
    services.py               reference implementations of semantic services

  runner/
    cli.py                    automation-run command
    plan_cli.py               direct automation-plan entry point
    execution.py              Python bundle execution
    plan_execution.py         declarative TestPlan execution
    validator.py              bundle validation

  steps/
    navigation_steps.py       logical component/state/property actions
    track_steps.py            reusable tracking actions
    threat_steps.py           reusable threat-state actions
    mosaic_steps.py           reusable mosaic actions
    camera_steps.py           camera/tracking reference actions
    validation_steps.py       reusable assertions

  resources/
    components.yaml           built-in reference component repository

  examples/
    plans/                    declarative plan examples
    reference_suite/          Python reference regression bundle
    reference_ui/             GUI/state/vision qualification bundle
```

---

# Recommended authoring discipline

For maintainable automation, use this order:

```text
1. Capture and name the object once.
2. Store durable multi-property identity in the repository.
3. Reuse an existing registered step whenever one already expresses the action.
4. Declare inputs explicitly.
5. Bind useful outputs explicitly.
6. Route outputs to later steps through named globals.
7. Wait/assert on observed object state rather than assuming an action succeeded.
8. Validate the plan and object repository before running.
9. Inspect execution-state/evidence on failure instead of adding blind sleeps.
10. Recapture/update repository identity centrally when the UI changes.
```

A test should therefore read primarily as semantic composition:

```yaml
- step: navigation.component.activate
  inputs:
    component_id: navigation.options_menu

- step: component.state.wait
  inputs:
    component_id: navigation.options_popup
    state_name: present
    expected: true

- step: component.property.get
  inputs:
    component_id: navigation.options_popup
    property_name: visible
  outputs:
    value: popup_visible

- step: validation.equal
  inputs:
    name: popup-visible
    actual:
      $var: popup_visible
    expected: true
```

rather than containing low-level accessibility tree walking, arbitrary coordinate clicking, or duplicated implementation logic.

That separation is the core design goal of Automation Harness: **capture object identity once, implement behavior once, and compose tests from the reusable pieces.**
