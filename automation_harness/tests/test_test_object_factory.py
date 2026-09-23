import pytest

from automation_harness.core.runtime_observation import CaptureEvidence, Framework, RuntimeObservation
from automation_harness.core.test_object_factory import TestObjectFactory
from automation_harness.models.component import ComponentState
from automation_harness.models.gui import ObjectType


def _observation(**changes):
    values = dict(
        framework=Framework.JAVAFX,
        process_id=42,
        window_id="Main",
        physical_class="javafx.scene.control.Button",
        semantic_role="button",
        name="Run",
        description=None,
        accessible_id="runButton",
        application="MSCT",
        properties={},
        state=ComponentState(present=True, visible=True, enabled=True),
        bounds=(1, 2, 30, 20),
        logical_children={},
        evidence=CaptureEvidence("javafx", True, ("PID endpoint",)),
    )
    values.update(changes)
    return RuntimeObservation(**values)


def test_factory_creates_backend_neutral_object_with_diagnostic_evidence():
    proposal = TestObjectFactory().materialize(
        _observation(), display_name="Run Button",
    )
    definition = proposal.definition
    assert definition.object_type is ObjectType.BUTTON
    assert definition.framework == "javafx"
    assert definition.strategies[0].options["identification"] == {
        "mandatory": {"accessible_id": "runButton", "semantic_role": "button"},
        "assistive": {
            "name": "Run", "window_id": "Main", "application": "MSCT",
            "physical_class": "javafx.scene.control.Button",
        },
    }
    assert definition.properties["capture_evidence"]["authoritative"] is True
    assert definition.scope["process_id"] == 42


def test_factory_promotes_javafx_menu_button_to_logical_menu_owner():
    proposal = TestObjectFactory().materialize(
        _observation(
            physical_class="javafx.scene.control.MenuButton",
            semantic_role="button",
            accessible_id="actionsMenu",
            logical_children={
                "save": {"kind": "menu_item", "criteria": {"id": "saveItem"}},
            },
        ),
        display_name="Actions Menu",
    )
    definition = proposal.definition
    assert definition.object_type is ObjectType.MENU
    assert definition.actions == frozenset({"resolve", "select_menu_item"})
    assert definition.subobjects["save"]["criteria"]["id"] == "saveItem"


@pytest.mark.parametrize("native_class", [
    "com.sun.javafx.scene.control.skin.MenuButtonSkin",
    "com.sun.javafx.scene.control.ContextMenuContent$MenuItemContainer",
])
def test_factory_rejects_transient_javafx_implementation_objects(native_class):
    with pytest.raises(ValueError, match="transient JavaFX"):
        TestObjectFactory().materialize(
            _observation(physical_class=native_class), display_name="Transient",
        )


def test_factory_requires_mandatory_identity_evidence():
    with pytest.raises(ValueError, match="no mandatory identity evidence"):
        TestObjectFactory().materialize(
            _observation(
                accessible_id=None, semantic_role=None, name=None,
                window_id=None, application=None, physical_class=None,
            ),
            display_name="Unknown",
        )


def test_factory_rejects_a_persisted_owner_missing_from_repository():
    class Repository:
        def contains(self, _object_id):
            return False

    with pytest.raises(ValueError, match="owner must resolve"):
        TestObjectFactory().materialize(
            _observation(), display_name="Child", repository=Repository(),
            owner_object_id="00000000-0000-0000-0000-000000000001",
        )


def test_rendered_identity_disallows_ordinal_fallback():
    observation = _observation(
        framework=Framework.RENDERED,
        physical_class="com.solipsys.Track",
        semantic_role="rendered_object",
        properties={
            "rendered_class": "com.solipsys.Track",
            "track_identity_key": "getTrackId",
            "track_identity_value": "A17",
        },
    )
    with pytest.raises(ValueError, match="do not permit ordinal"):
        TestObjectFactory().materialize(
            observation, display_name="Track A17", ordinal=0,
        )
