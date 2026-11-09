from automation_harness.authoring.capture_property_policy import (
    available_properties,
    property_policy,
)


def test_strong_semantic_properties_are_selected_by_default():
    assert property_policy("id", "cameraSelector").selected is True
    assert property_policy("class", "edu.mit.ll.ersa.FeedPanel").selected is True
    assert property_policy("accessible_text", "Camera Selector").selected is True
    assert property_policy("properties.automation.feed-id", "Camera12").selected is True


def test_runtime_and_weak_properties_remain_available_but_unselected():
    focused = property_policy("focused", False)
    bounds = property_policy("bounds", [10, 20, 30, 40])
    layout = property_policy("layout.grid_column", 2)

    assert focused.selectable is True and focused.selected is False
    assert bounds.selectable is True and bounds.selected is False
    assert layout.selectable is True and layout.selected is False


def test_inferred_ordinal_is_weak_but_remains_selected():
    ordinal = property_policy("ordinal", 3, candidate_section="assistive")
    assert ordinal.stability == "low"
    assert ordinal.selectable is True
    assert ordinal.selected is True


def test_mandatory_fallback_stays_selected_even_when_weak():
    role = property_policy("accessible_role", "PARENT", candidate_section="mandatory")
    layout = property_policy("layout.grid_column", 2, candidate_section="mandatory")
    internal = property_policy(
        "class",
        "com.sun.javafx.scene.control.MenuBarButton",
        candidate_section="mandatory",
    )

    assert role.selected is True
    assert layout.selected is True
    assert internal.selected is True


def test_inherited_and_common_candidate_evidence_is_locked_but_retained():
    inherited = property_policy(
        "window",
        "MVD",
        candidate_section="assistive",
        source="inherited",
    )
    common = property_policy(
        "class",
        "edu.mit.ll.ersa.FeedPanel",
        candidate_section="assistive",
        source="common",
    )

    assert inherited.selected is True and inherited.selectable is False
    assert common.selected is True and common.selectable is False


def test_session_bridge_metadata_cannot_be_authored():
    policy = property_policy("ref", "n42")
    assert policy.stability == "session"
    assert policy.selected is False
    assert policy.selectable is False


def test_available_properties_collects_broad_evidence_without_dropping_runtime_values():
    payload = {
        "ref": "n42",
        "id": "feedPanel",
        "class": "edu.mit.ll.ersa.FeedPanel",
        "visible": True,
        "focused": False,
        "bounds": [1.0, 2.0, 300.0, 200.0],
        "style_classes": ["feed", "selected"],
        "stable_ancestors": [{"id": "videoGrid", "class": "javafx.scene.layout.GridPane"}],
        "layout": {"grid_row": 1, "grid_column": 2},
        "properties": {
            "automation.feed-id": "Camera12",
            "domain.mode": "tracking",
        },
    }

    values = available_properties(payload)

    assert values["id"] == "feedPanel"
    assert values["visible"] is True
    assert values["focused"] is False
    assert values["bounds"] == [1.0, 2.0, 300.0, 200.0]
    assert values["style_classes"] == ["feed", "selected"]
    assert values["layout.grid_row"] == 1
    assert values["layout.grid_column"] == 2
    assert values["properties.automation.feed-id"] == "Camera12"
    assert values["properties.domain.mode"] == "tracking"
    assert values["lineage"][0]["id"] == "videoGrid"
    assert values["ref"] == "n42"


def test_non_javafx_extra_properties_are_diagnostic_until_backend_supports_them():
    policy = property_policy("focused", False, framework="atspi")
    assert policy.selectable is False
    assert policy.selected is False


def test_native_java_locator_fields_remain_editable_but_are_not_automatically_added():
    native_class = property_policy(
        "native_class", "org.example.trackstate.UiTrackStateDBStatusIndicator",
        candidate_section="mandatory", framework="swing",
    )
    path = property_policy(
        "component_path", "javax.swing.JFrame[0]/javax.swing.JMenu[0]",
        candidate_section="assistive", framework="swing",
    )
    window = property_policy(
        "window", "Demo Display", framework="swing",
    )
    assert native_class.selected and native_class.selectable
    assert path.selected and path.selectable and path.stability == "low"
    assert not window.selected and window.selectable


def test_native_java_observation_fields_are_read_only_diagnostics():
    for key, value in (
        ("accessible_name", "Object"),
        ("accessible_description", "Status indicator"),
        ("accessible_role", "menu"),
        ("tooltip_text", "Track state"),
        ("properties.semantic_ancestors", [{"name": "Menu Bar"}]),
    ):
        policy = property_policy(key, value, framework="swing")
        assert not policy.selected and not policy.selectable
        assert policy.stability == "diagnostic"


def test_transient_process_id_is_never_an_authored_locator():
    policy = property_policy(
        "properties.process_id", 5908, framework="swing",
    )
    assert policy.stability == "session"
    assert not policy.selectable


def test_available_properties_carries_native_accessibility_evidence():
    values = available_properties({
        "name": "Object",
        "native_class": "org.example.trackstate.UiTrackStateDBStatusIndicator",
        "accessible_name": "Object",
        "accessible_description": "Track state indicator",
        "accessible_role": "menu",
        "component_name": "trackStateIndicator",
        "tooltip_text": "Track state",
        "window": "Demo Display",
        "capture_source": "stored_repository_definition",
    })
    assert values["native_class"].endswith("UiTrackStateDBStatusIndicator")
    assert values["accessible_name"] == "Object"
    assert values["accessible_description"] == "Track state indicator"
    assert values["window"] == "Demo Display"
    assert values["capture_source"] == "stored_repository_definition"
