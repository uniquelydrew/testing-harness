"""Tests for semantic window identification and screenshot provenance."""
import pytest

from automation_harness.core.window_identity_capture import (
    identify_captured_window, validated_window_screen_region,
)
from automation_harness.models.component import CapturedComponent, ComponentState


def capture(role="frame", name="Main Window", bounds=(10, 20, 300, 200)):
    return CapturedComponent(
        name=name, role=role, description=None, accessible_id=None,
        application="ersa", hierarchy=(), actions=(), bounds=bounds,
        state=ComponentState(), backend_properties={"process_id": 123},
    )


def test_window_identity_excludes_runtime_pid():
    identity = identify_captured_window(capture())
    assert identity.title == "Main Window"
    assert identity.application.application_id == "ersa"
    assert not hasattr(identity, "process_id")


def test_reject_non_window_capture():
    with pytest.raises(ValueError):
        identify_captured_window(capture(role="push button"))


def test_region_requires_unobstructed_window():
    window = capture()
    with pytest.raises(ValueError):
        validated_window_screen_region(window, unobstructed=False)
    assert validated_window_screen_region(window, unobstructed=True) == (10, 20, 300, 200)
