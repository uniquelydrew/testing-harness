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
