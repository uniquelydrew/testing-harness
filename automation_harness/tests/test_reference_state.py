import pytest

from automation_harness.reference.state import ReferenceState


def test_reference_ui_metadata_is_inspection_only():
    state = ReferenceState()
    state.register_ui_component("threat.high", text="High")

    component = state.handle("ui_component", {"component_id": "threat.high"})

    assert component["present"] is True
    assert component["text"] == "High"


def test_reference_protocol_exposes_no_ui_activation_backdoor():
    state = ReferenceState()
    state.register_ui_component("threat.high", text="High")

    with pytest.raises(ValueError, match="unknown action: ui_activate"):
        state.handle("ui_activate", {"component_id": "threat.high"})

    assert state.snapshot()["threat_level"] == "LOW"


def test_reference_ui_component_state_can_model_expanded_and_disabled():
    from automation_harness.reference.state import ReferenceState

    state = ReferenceState()
    state.register_ui_component("menu", enabled=True, expanded=False)
    state.register_ui_component("disabled", enabled=False)
    assert state.handle("ui_component", {"component_id": "disabled"})["enabled"] is False
    assert state.handle("ui_component", {"component_id": "menu"})["expanded"] is False
    changed = state.handle("set_ui_component_state", {"component_id": "menu", "expanded": True})
    assert changed["expanded"] is True
