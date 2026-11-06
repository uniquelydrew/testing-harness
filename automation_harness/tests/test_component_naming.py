from automation_harness.core.component_naming import (
    default_component_name,
    default_payload_name,
    unique_component_name,
)
from automation_harness.models.component import CapturedComponent, ComponentState
from automation_harness.models.gui import ObjectType


def _capture(name=None, accessible_id=None, object_type=ObjectType.BUTTON, native_class=None, properties=None):
    return CapturedComponent(
        name=name,
        role=object_type.value.replace("_", " "),
        description=None,
        accessible_id=accessible_id,
        application="Demo",
        window="Demo",
        hierarchy=(),
        actions=(),
        bounds=(0, 0, 10, 10),
        state=ComponentState(present=True),
        backend_properties=properties or {},
        object_type=object_type,
        native_class=native_class,
    )


def test_default_name_combines_distinguishing_text_and_type():
    assert default_component_name(_capture(name="Cancel")) == "Cancel Button"


def test_existing_type_suffix_is_not_duplicated():
    assert default_component_name(_capture(name="Cancel Button")) == "Cancel Button"


def test_text_field_label_does_not_duplicate_field_word():
    assert default_component_name(
        _capture(name="Username Field", object_type=ObjectType.TEXT_FIELD)
    ) == "Username Field"


def test_no_semantic_text_falls_back_to_canonical_type():
    assert default_component_name(
        _capture(
            name="javafx.scene.control.Button",
            object_type=ObjectType.BUTTON,
            native_class="javafx.scene.control.Button",
        )
    ) == "Button"


def test_conflicts_increment_with_readable_numeric_suffix():
    capture = _capture(name="Cancel")
    assert unique_component_name({"Cancel Button", "Cancel Button 2"}, capture) == "Cancel Button 3"



def test_capture_payload_name_uses_text_and_canonical_type():
    assert default_payload_name({
        "object_type": "button",
        "text": "Cancel",
        "class": "javax.swing.JButton",
    }) == "Cancel Button"


def test_capture_payload_name_humanizes_camel_case_ids_without_doubling_type():
    assert default_payload_name({
        "object_type": "button",
        "id": "cancelButton",
        "class": "javafx.scene.control.Button",
    }) == "Cancel Button"


def test_capture_payload_name_drops_framework_class_fallback():
    assert default_payload_name({
        "object_type": "text_field",
        "name": "javax.swing.JTextField",
        "class": "javax.swing.JTextField",
    }) == "Text Field"


def test_javafx_choicebox_name_preserves_concrete_selector_type():
    assert default_component_name(
        _capture(
            name="Site",
            object_type=ObjectType.COMBO_BOX,
            native_class="javafx.scene.control.ChoiceBox",
        )
    ) == "Site Choice Box"


def test_javafx_combobox_name_preserves_concrete_selector_type():
    assert default_component_name(
        _capture(
            name="Camera",
            object_type=ObjectType.COMBO_BOX,
            native_class="javafx.scene.control.ComboBox",
        )
    ) == "Camera Combo Box"


def test_javafx_choicebox_payload_name_preserves_concrete_selector_type():
    assert default_payload_name({
        "object_type": "combo_box",
        "text": "Site",
        "class": "javafx.scene.control.ChoiceBox",
    }) == "Site Choice Box"


def test_javafx_capture_name_uses_semantic_leaf_not_hierarchy():
    capture = CapturedComponent(
        name="File",
        role="menu",
        description=None,
        accessible_id="fileMenu",
        application="MVD",
        window="MVD",
        hierarchy=("AnchorPane#AnchorPane", "MenuBar", "HBox", "MenuBarButton#fileMenu"),
        actions=("activate",),
        bounds=(9, 65, 43, 29),
        state=ComponentState(present=True, visible=True, showing=True, enabled=True),
        backend_properties={"text": "File"},
        object_type=ObjectType.MENU,
        framework="javafx",
        native_class="com.sun.javafx.scene.control.MenuBarButton",
    )

    assert default_component_name(capture) == "File Menu"
    assert "AnchorPane" not in default_component_name(capture)


def test_native_application_class_is_used_instead_of_generic_object_name():
    capture = _capture(
        name="Object",
        object_type=ObjectType.MENU,
        native_class="org.example.trackstate.UiTrackStateDBStatusIndicator",
    )
    assert default_component_name(capture) == "Track State DB Status Indicator"


def test_serialized_capture_uses_native_class_if_accessible_name_is_generic():
    assert default_payload_name({
        "object_type": "menu",
        "accessible_text": "Object",
        "native_class": "org.example.trackstate.UiTrackStateDBStatusIndicator",
    }) == "Track State DB Status Indicator"


def test_meaningful_accessible_text_takes_priority_over_class_fallback():
    capture = _capture(
        name="Track Alerts",
        object_type=ObjectType.BUTTON,
        native_class="org.example.trackstate.UiTrackStateDBStatusIndicator",
    )
    assert default_component_name(capture) == "Track Alerts Button"


def test_framework_classes_do_not_become_generic_fallback_names():
    assert default_component_name(_capture(
        name="Object",
        object_type=ObjectType.MENU,
        native_class="javax.swing.JMenu",
    )) == "Menu"
