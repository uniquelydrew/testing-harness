from automation_harness.core.interaction_preparation import (
    InteractionPreparation,
    PreparationOutcome,
    preparation_requirement,
    validate_preparation,
)
from automation_harness.models.gui import ActionType


def test_pointer_actions_raise_window_without_preemptive_component_focus():
    requirement = preparation_requirement(ActionType.CLICK)
    assert requirement.activate_window
    assert requirement.require_window_activation
    assert not requirement.request_focus
    assert not requirement.require_focus


def test_text_actions_require_verified_component_focus():
    requirement = preparation_requirement(ActionType.SET_TEXT)
    assert requirement.activate_window
    assert requirement.request_focus
    assert requirement.require_focus


def test_semantic_activation_attempts_focus_without_requiring_focusability():
    requirement = preparation_requirement(ActionType.ACTIVATE)
    assert requirement.activate_window
    assert requirement.request_focus
    assert not requirement.require_focus


def test_required_focus_failure_is_rejected():
    preparation = InteractionPreparation(
        "atspi",
        PreparationOutcome("activate_window", True, True),
        PreparationOutcome("focus", True, False, error="focus state did not change"),
    )
    try:
        validate_preparation(preparation_requirement(ActionType.SET_TEXT), preparation)
    except RuntimeError as exc:
        assert "focus state did not change" in str(exc)
    else:
        raise AssertionError("required focus failure was accepted")


def test_non_focusable_semantic_action_can_continue():
    preparation = InteractionPreparation(
        "atspi",
        PreparationOutcome("activate_window", True, True),
        PreparationOutcome("focus", True, False, supported=False, error="not focusable"),
    )
    validate_preparation(preparation_requirement(ActionType.ACTIVATE), preparation)
