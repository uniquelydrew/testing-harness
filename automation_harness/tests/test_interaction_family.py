import pytest

from automation_harness.core.interaction_family import InteractionFamily, interaction_family
from automation_harness.models.gui import ActionType, ObjectType, default_actions


@pytest.mark.parametrize(("object_type", "native_class", "expected"), [
    (ObjectType.COMBO_BOX, "javafx.scene.control.ComboBox", InteractionFamily.POPUP_SELECTOR),
    (ObjectType.COMBO_BOX, "javafx.scene.control.ChoiceBox", InteractionFamily.POPUP_SELECTOR),
    (ObjectType.DATE_PICKER, "javafx.scene.control.DatePicker", InteractionFamily.POPUP_SELECTOR),
    (ObjectType.COLOR_PICKER, "javafx.scene.control.ColorPicker", InteractionFamily.POPUP_SELECTOR),
    (ObjectType.MENU, "javafx.scene.control.MenuButton", InteractionFamily.MENU_OWNER),
    (ObjectType.MENU, "javafx.scene.control.SplitMenuButton", InteractionFamily.MENU_OWNER),
    (ObjectType.TOGGLE_BUTTON, "javafx.scene.control.ToggleButton", InteractionFamily.SELECTABLE),
    (ObjectType.CHECK_BOX, "javafx.scene.control.CheckBox", InteractionFamily.SELECTABLE),
    (ObjectType.RADIO_BUTTON, "javafx.scene.control.RadioButton", InteractionFamily.SELECTABLE),
    (ObjectType.TEXT_FIELD, "javafx.scene.control.TextField", InteractionFamily.TEXT_INPUT),
    (ObjectType.PASSWORD_FIELD, "javafx.scene.control.PasswordField", InteractionFamily.TEXT_INPUT),
    (ObjectType.TEXT_AREA, "javafx.scene.control.TextArea", InteractionFamily.TEXT_INPUT),
    (ObjectType.SLIDER, "javafx.scene.control.Slider", InteractionFamily.VALUE_CONTROL),
    (ObjectType.SPINNER, "javafx.scene.control.Spinner", InteractionFamily.VALUE_CONTROL),
    (ObjectType.LIST, "javafx.scene.control.ListView", InteractionFamily.COLLECTION_SELECTOR),
    (ObjectType.TREE, "javafx.scene.control.TreeView", InteractionFamily.COLLECTION_SELECTOR),
    (ObjectType.TABLE, "javafx.scene.control.TableView", InteractionFamily.COLLECTION_SELECTOR),
    (ObjectType.TREE_TABLE, "javafx.scene.control.TreeTableView", InteractionFamily.COLLECTION_SELECTOR),
    (ObjectType.TAB_CONTAINER, "javafx.scene.control.TabPane", InteractionFamily.COLLECTION_SELECTOR),
])
def test_interaction_family_contract(object_type, native_class, expected):
    assert interaction_family(object_type, native_class) is expected


@pytest.mark.parametrize("object_type", [ObjectType.COMBO_BOX, ObjectType.DATE_PICKER, ObjectType.COLOR_PICKER])
def test_popup_selector_object_types_keep_select_item_public_action(object_type):
    assert ActionType.SELECT_ITEM in default_actions(object_type)


@pytest.mark.parametrize(("role", "native_class", "expected"), [
    ("TAB_PANE", "edu.mit.ll.ersa.common.dashboard.components.AdvancedDetailsDisplay", ObjectType.TAB_CONTAINER),
    ("tab pane", "edu.mit.ll.ersa.common.dashboard.components.AdvancedDetailsDisplay", ObjectType.TAB_CONTAINER),
    ("tabpane", "javafx.scene.control.TabPane", ObjectType.TAB_CONTAINER),
    ("page tab list", "javax.swing.JTabbedPane", ObjectType.TAB_CONTAINER),
    ("page tab", "javafx.scene.control.Tab", ObjectType.TAB),
    ("tab", "javafx.scene.control.Tab", ObjectType.TAB),
])
def test_tab_owner_and_logical_tab_are_distinct_object_types(role, native_class, expected):
    from automation_harness.models.gui import classify_accessibility

    assert classify_accessibility(role, native_class) is expected


def test_javafx_custom_tabpane_capture_is_collection_selector_not_single_tab():
    from automation_harness.drivers.javafx_bridge import _captured_recording_node

    capture = _captured_recording_node({
        "framework": "javafx",
        "class": "edu.mit.ll.ersa.common.dashboard.components.AdvancedDetailsDisplay",
        "accessible_role": "TAB_PANE",
        "window": "Advanced Details",
        "tabs": [
            {"index": 0, "id": None, "text": "Camera Status"},
            {"index": 3, "id": "sessionViewTab", "text": "Active Sessions"},
        ],
    })

    assert capture.semantic_type() is ObjectType.TAB_CONTAINER
    assert capture.backend_properties["tabs"][0]["text"] == "Camera Status"
    assert capture.backend_properties["tabs"][1]["id"] == "sessionViewTab"
    assert interaction_family(capture.semantic_type(), capture.native_class) is InteractionFamily.COLLECTION_SELECTOR
    assert ActionType.SELECT_ITEM in default_actions(capture.semantic_type())
