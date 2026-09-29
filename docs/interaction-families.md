# JavaFX interaction families

The author-facing `ObjectType` remains the semantic identity stored in an
Object Repository. Interaction families are a separate, internal axis that
defines capture and interaction mechanics.

| Family | Members |
| --- | --- |
| `popup_selector` | ComboBox, ChoiceBox, DatePicker, ColorPicker |
| `menu_owner` | MenuBar, MenuButton, SplitMenuButton, Menu, ContextMenu |
| `selectable` | ToggleButton, CheckBox, RadioButton |
| `text_input` | TextField, PasswordField, TextArea |
| `value_control` | Slider, Spinner |
| `collection_selector` | ListView, TreeView, TableView, TreeTableView, TabPane |

`popup_selector` recording is a transaction: opening the owner and selecting
a transient popup node produce one `select_item` interaction on the durable
owner. The JavaFX bridge uses `popup_selection` to carry its owner, selected
value, selected index, and family. The owner value/selection model is preferred
over cell text, which is particularly important for ChoiceBox's ContextMenu /
MenuItem popup topology.

The following concrete JavaFX checks intentionally remain:

- The JavaFX agent's `interactionFamily` and the Java agent's
  `JavaFxInteractionFamily` are the runtime-native mappings. They are the only
  class lists used to choose family behavior; contract tests cover their Python
  counterpart and Java resolver smoke coverage covers the native mapping.
- `ListCell`, `MenuItem`, calendar cells, and color tiles remain named only as
  transient implementation nodes. They are never semantic owners and are
  blocked from repository persistence.
- Logical menu traversal continues to name `Menu` and `MenuItem` because these
  are JavaFX's public route model, not a physical-click special case.
