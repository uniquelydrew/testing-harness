from automation_harness.core.object_capture import ObjectCaptureService
from automation_harness.models.component import CapturedComponent, ComponentState, ComponentStrategy


class _Stage:
    matches = 10


class _Driver:
    available = True

    def assess_identification(self, identity):
        return (_Stage(),)


def test_recorded_explicit_ordinal_is_preserved_for_repeated_controls():
    captured = CapturedComponent(
        name="2", role="push button", description=None, accessible_id=None,
        application="Calculator", hierarchy=(), actions=("click",), bounds=None,
        state=ComponentState(True),
        authored_strategy=ComponentStrategy("atspi", {"identification": {
            "mandatory": {"name": "2", "role": "push button"},
            "assistive": {"application": "Calculator"},
            "ordinal": 4,
        }}),
    )
    identity = ObjectCaptureService(_Driver())._best_identification(captured)
    assert identity.ordinal == 4
