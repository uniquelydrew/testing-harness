from __future__ import annotations

import threading

from automation_harness.core.component_repository import ComponentRepository
from automation_harness.models.component import CapturedComponent, ComponentState
from automation_harness.models.gui import ActionType, ObjectType
from automation_harness.recording import ActionFired, PointerInteraction, RecordingSession, StateChanged, TextChanged, interactions_to_steps
from automation_harness.recording.evidence import parameters_for_pointer


def _capture(name: str, *, kind: ObjectType = ObjectType.BUTTON) -> CapturedComponent:
    return CapturedComponent(
        name=name, role="button" if kind is ObjectType.BUTTON else "text", description=None,
        accessible_id=None, application="Demo", window="Demo", hierarchy=("Demo", name),
        actions=("activate",), bounds=(10, 10, 100, 24), state=ComponentState(present=True),
        framework="javafx", native_class=f"javafx.scene.control.{kind.value}", object_type=kind,
    )


def _repository() -> ComponentRepository:
    return ComponentRepository.from_document({"version": 2, "components": {
        "open": {"object_type": "button", "actions": ["click"], "framework": "javafx", "strategies": [{"type": "atspi", "identification": {"mandatory": {"name": "Open", "role": "button"}}}]},
    }})


def test_click_correlates_action_and_meaningful_state_without_pressed_noise():
    session = RecordingSession(repository=_repository())
    target = _capture("Open")
    session.start()
    session.observe(PointerInteraction(1.0, "javafx", target, phase="released", coordinates=(15, 15)))
    session.observe(StateChanged(1.1, "javafx", target, property="pressed", before=True, after=False))
    session.observe(ActionFired(1.2, "javafx", target, action="fire"))
    session.observe(StateChanged(1.3, "javafx", target, property="enabled", before=True, after=False))
    interactions = session.stop()
    assert len(interactions) == 1
    interaction = interactions[0]
    assert interaction.action.value == "click"
    assert interaction.repository_match.component_id == "open"
    assert [(delta.property, delta.before, delta.after) for delta in interaction.resulting_changes] == [("enabled", True, False)]
    assert "coordinates" not in interaction.parameters
    assert interactions_to_steps(interactions)[0].inputs["action"] == {"type": "click"}


def test_text_changes_coalesce_to_final_value_and_stop_flushes_pending_interaction():
    session = RecordingSession()
    target = _capture("Search", kind=ObjectType.TEXT_FIELD)
    session.start()
    session.observe(TextChanged(1.0, "javafx", target, before="", after="h"))
    session.observe(TextChanged(1.1, "javafx", target, before="h", after="hello"))
    interactions = session.stop()
    assert len(interactions) == 1
    assert interactions[0].action.value == "set_text"
    assert interactions[0].parameters == {"value": "hello"}
    assert interactions[0].repository_match.status == "new_candidate"


def test_direct_toggle_state_promotes_a_pointer_click_to_a_semantic_toggle():
    session = RecordingSession()
    target = _capture("Enabled", kind=ObjectType.CHECK_BOX)
    session.start()
    session.observe(PointerInteraction(1.0, "javafx", target, phase="released"))
    session.observe(StateChanged(1.1, "javafx", target, property="checked", before=False, after=True))
    assert session.stop()[0].action.value == "toggle"


def test_duplicate_cross_backend_pointer_observations_prefer_javafx_target():
    atspi = CapturedComponent(
        **{
            **_capture("Open").__dict__,
            "framework": "atspi",
            "role": "button",
            "application": "Demo",
            "window": "Demo",
        }
    )
    javafx = _capture("Open")
    session = RecordingSession()
    session.start()
    session.observe(PointerInteraction(1.0, "atspi", atspi, phase="released"))
    session.observe(PointerInteraction(1.05, "javafx", javafx, phase="released"))

    interactions = session.stop()

    assert len(interactions) == 1
    assert interactions[0].target.framework == "javafx"
    assert interactions[0].evidence["correlated_sources"] == ["atspi", "javafx"]


def test_modal_visibility_is_retained_as_contextual_effect_of_click():
    session = RecordingSession()
    session.start()
    session.observe(PointerInteraction(1.0, "javafx", _capture("Open"), phase="released"))
    session.observe(StateChanged(1.2, "javafx", _capture("Open dialog", kind=ObjectType.DIALOG), property="showing", before=False, after=True))
    changes = session.stop()[0].resulting_changes
    assert [(item.component.name, item.property, item.after) for item in changes] == [("Open dialog", "showing", True)]


def test_raw_observations_are_bounded_and_disabled_without_diagnostics():
    session = RecordingSession(diagnostics=True, diagnostic_limit=2)
    target = _capture("Open")
    session.start()
    for timestamp in (1.0, 2.0, 3.0):
        session.observe(PointerInteraction(timestamp, "javafx", target, phase="moved"))
    assert len(session.observations()) == 2
    session.stop()


def test_stop_correlates_final_adapter_event_before_closing_session():
    target = _capture("Open")

    class Adapter:
        def start(self, _emit): self.emit = _emit
        def stop(self): self.emit(PointerInteraction(1.0, "javafx", target, phase="released"))

    session = RecordingSession((Adapter(),))
    session.start()
    assert [item.action.value for item in session.stop()] == ["click"]


def test_observations_from_concurrent_adapters_are_serialized():
    session = RecordingSession(diagnostics=True, diagnostic_limit=64)
    barrier = threading.Barrier(3)

    def emit(prefix):
        barrier.wait()
        for index in range(20):
            session.observe(PointerInteraction(
                float(index), prefix, _capture(f"{prefix}-{index}"), phase="released",
            ))

    session.start()
    workers = [threading.Thread(target=emit, args=(source,)) for source in ("atspi", "javafx")]
    for worker in workers:
        worker.start()
    barrier.wait()
    for worker in workers:
        worker.join(timeout=2)
        assert not worker.is_alive()

    interactions = session.stop()
    assert len(interactions) == 40
    assert len({item.target.name for item in interactions}) == 40
    assert len(session.observations()) == 40


def test_concurrent_stop_calls_only_stop_adapters_once():
    class Adapter:
        def __init__(self):
            self.stops = 0

        def start(self, emit):
            self.emit = emit

        def stop(self):
            self.stops += 1
            self.emit(PointerInteraction(1.0, "javafx", _capture("Open"), phase="released"))

    adapter = Adapter()
    session = RecordingSession((adapter,))
    session.start()
    barrier = threading.Barrier(3)
    results = []

    def stop():
        barrier.wait()
        results.append(session.stop())

    workers = [threading.Thread(target=stop) for _ in range(2)]
    for worker in workers:
        worker.start()
    barrier.wait()
    for worker in workers:
        worker.join(timeout=2)
        assert not worker.is_alive()

    assert adapter.stops == 1
    assert all(len(result) == 1 for result in results)


def test_evidence_policy_keeps_geometry_only_for_geometry_dependent_targets():
    assert parameters_for_pointer(ActionType.CLICK, _capture("Open"), (1, 2)) == {}
    canvas = _capture("Chart", kind=ObjectType.CANVAS)
    assert parameters_for_pointer(ActionType.CLICK, canvas, (1, 2)) == {"coordinates": [1, 2]}
