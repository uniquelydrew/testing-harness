# JavaFX Automation Identity Contract

The JavaFX bridge resolves objects from semantic evidence first and structural evidence second. Application source should expose meaning where possible so automation does not depend on JavaFX skin classes, literal tree paths, or screen coordinates.

## Preferred identity order

1. Explicit `Node.id` / stable FXML `id`
2. Application-authored scalar `Node.getProperties()` values using an automation/test namespace
3. Scalar `Node.userData`
4. Stable accessible text/text plus semantic role
5. Application/public JavaFX class
6. Stable ancestor lineage
7. Stable layout constraints such as `GridPane` row/column
8. Scoped ordinal as a final discriminator

Literal hierarchy and JavaFX internal classes such as `com.sun.javafx.*` are diagnostic evidence, not preferred durable identity.

## Source hooks

### Stable Node ID

FXML:

```xml
<VideoPlayerFXMLController id="feedPanelNorthWest" ... />
```

Java:

```java
feedPanel.setId("feedPanelNorthWest");
```

Use an ID when the logical object itself is stable across runs and data assignments.

### Domain identity property

For repeated/data-driven components, expose the represented domain object without changing CSS semantics:

```java
feedPanel.getProperties().put("automation.feed-id", feedId);
```

The bridge automatically treats scalar keys beginning with `automation.`, `test.`, or `qa.` as very-high-stability identity evidence. Examples:

```java
node.getProperties().put("automation.id", "mvd.primary-video");
node.getProperties().put("automation.feed-id", cameraId);
node.getProperties().put("automation.role", "tracking-feed");
```

Only scalar values are exported: strings, numbers, booleans, characters, and enums. Arbitrary application objects are intentionally not serialized.

### User data

A scalar `userData` value is also exported and can identify a data-bound object:

```java
feedPanel.setUserData(cameraId);
```

Prefer a namespaced `automation.*` property when multiple independent metadata values are useful.

## Structural identity

The bridge exports a stable ancestor lineage rather than relying only on the literal scene-graph path. Stable ancestors are selected when they have an explicit ID, meaningful text/accessibility metadata, or an application-defined class. Internal JavaFX skin nodes are omitted from stable lineage.

A locator may therefore resemble:

```yaml
mandatory:
  class: edu.mit.ll.ersa.common.dashboard.components.VideoPlayerFXMLController
assistive:
  lineage:
    - id: bigGridPane
      class: javafx.scene.layout.GridPane
  layout:
    grid_row: 1
    grid_column: 2
```

Lineage matching is ordered but tolerant of unrelated nodes inserted between stable ancestors.

## Repeated feed panels

If eight feed panels have stable physical slots, `GridPane` row/column is appropriate structural identity:

```yaml
mandatory:
  class: edu.mit.ll.ersa.common.dashboard.components.VideoPlayerFXMLController
assistive:
  lineage:
    - id: bigGridPane
  layout:
    grid_row: 1
    grid_column: 2
```

If feeds can move between slots, row/column is state rather than identity. Expose the represented feed instead:

```java
feedPanel.getProperties().put("automation.feed-id", cameraId);
```

Then the generated identity can be based on:

```yaml
mandatory:
  properties:
    automation.feed-id: camera-12
assistive:
  class: edu.mit.ll.ersa.common.dashboard.components.VideoPlayerFXMLController
```

## Ordinals

An ordinal is generated only when all selected semantic and structural conditions still match multiple objects. It is scoped to that already-filtered candidate set; it is not a global scene-graph index. Treat ordinal-based identity as a fallback and prefer adding application metadata when the underlying objects have distinct logical meaning.
