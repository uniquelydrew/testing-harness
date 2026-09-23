from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.javafx_menu_execution import _select_rendered_terminal
from automation_harness.core.logical_menu import stage_recorded_menu_capture
from automation_harness.models.component import CapturedComponent, ComponentState
from automation_harness.models.gui import ObjectType
from automation_harness.recording import RecordingSession


def _logical_context_item():
    return CapturedComponent(
        name="Camera Selector",
        role="menu item",
        description=None,
        accessible_id="cameraSelectorMenuItem",
        application="ContextMenu",
        window="ContextMenu",
        hierarchy=(),
        actions=("activate",),
        bounds=(100, 200, 120, 24),
        state=ComponentState(present=True, visible=True, showing=True, enabled=True),
        backend_properties={
            "logical_menu": {
                "path": [
                    {
                        "kind": "menu item",
                        "criteria": {
                            "id": "cameraSelectorMenuItem",
                            "text": "Camera Selector",
                        },
                    }
                ],
                "owner": {
                    "kind": "context menu",
                    "popup": {
                        "class": "javafx.scene.control.ContextMenu",
                        "role": "context menu",
                    },
                },
            }
        },
        framework="javafx",
        native_class="javafx.scene.control.MenuItem",
        object_type=ObjectType.MENU_ITEM,
    )


def test_recording_observation_does_not_create_context_menu_until_staged():
    repository = ComponentRepository({})
    session = RecordingSession(repository=repository)
    capture = _logical_context_item()

    match = session._match(capture)

    assert match.status == "new_candidate"
    assert repository.components == {}

    updated, target, owner_created, inventory_changed = stage_recorded_menu_capture(
        repository, capture,
    )

    assert owner_created is True
    assert inventory_changed is True
    assert target.owner_component_id == "Context Menu"
    assert target.subobject_path == ("cameraselectormenuitem",)
    owner = updated.components["Context Menu"]
    assert owner.object_type == ObjectType.CONTEXT_MENU
    assert owner.native_class == "javafx.scene.control.ContextMenu"
    assert owner.subobjects["cameraselectormenuitem"]["criteria"] == {
        "id": "cameraSelectorMenuItem",
        "text": "Camera Selector",
    }


def test_repeated_context_menu_staging_reuses_owner_and_subobject():
    repository = ComponentRepository({})
    capture = _logical_context_item()

    repository, first, first_created, first_changed = stage_recorded_menu_capture(
        repository, capture,
    )
    repository, second, second_created, second_changed = stage_recorded_menu_capture(
        repository, capture,
    )

    assert first == second
    assert first_created is True
    assert first_changed is True
    assert second_created is False
    assert second_changed is False
    assert list(repository.components) == ["Context Menu"]
    assert list(repository.components["Context Menu"].subobjects) == ["cameraselectormenuitem"]


def test_context_menu_execution_falls_back_to_durable_terminal_selector(monkeypatch):
    clicked = []

    class Endpoint:
        pid = 4136

    class Stage:
        def to_dict(self):
            return {"source": "mandatory", "criteria": {"id": "cameraSelectorMenuItem"}, "matches": 1}

    class Driver:
        def _find_unique(self, identification):
            assert identification == {
                "mandatory": {
                    "id": "cameraSelectorMenuItem",
                    "text": "Camera Selector",
                }
            }
            return Endpoint(), {"bounds": [100, 200, 120, 24]}, (Stage(),)

    monkeypatch.setattr(
        "automation_harness.core.pointer_actions.click_bounds",
        lambda bounds, action: clicked.append((bounds, action)) or {"clicked": True},
    )

    result = _select_rendered_terminal(
        Driver(),
        [{
            "kind": "menu_item",
            "criteria": {
                "id": "cameraSelectorMenuItem",
                "text": "Camera Selector",
            },
        }],
        owner_error=LookupError("ContextMenu root is transient"),
    )

    assert result["fallback"] == "rendered_terminal"
    assert result["selector"]["id"] == "cameraSelectorMenuItem"
    assert clicked and clicked[0][0] == (100, 200, 120, 24)
