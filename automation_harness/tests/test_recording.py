from __future__ import annotations

import threading
from dataclasses import replace

from automation_harness.core.component_repository import ComponentRepository
from automation_harness.models.component import (
    CapturedComponent, ComponentDefinition, ComponentState, ComponentStrategy,
)
from automation_harness.models.gui import ActionType, ObjectType
from automation_harness.recording import ActionFired, PointerInteraction, RecordingSession, StateChanged, TextChanged, interactions_to_steps
from automation_harness.recording.evidence import parameters_for_pointer
from automation_harness.recording.diagnostics import RecordingDebugLog


def _capture(name: str, *, kind: ObjectType = ObjectType.BUTTON) -> CapturedComponent:
    return CapturedComponent(
        name=name, role="button" if kind is ObjectType.BUTTON else "text", description=None,
        accessible_id=None, application="Demo", window="Demo", hierarchy=("Demo", name),
        actions=("activate",), bounds=(10, 10, 100, 24), state=ComponentState(present=True),
        framework="javafx", native_class=f"javafx.scene.control.{kind.value}", object_type=kind,
    )


def _repository() -> ComponentRepository:
    return ComponentRepository.from_document({"version": 3, "components": {
        "open": {"object_id": "11111111-1111-4111-8111-111111111111", "object_type": "button", "actions": ["click"], "framework": "javafx", "strategies": [{"type": "atspi", "identification": {"mandatory": {"name": "Open", "role": "button"}}}]},
    }})


def _javafx_region(index: int, *, node_ref: str) -> CapturedComponent:
    lineage = [
        {"accessible_role": "TITLED_PANE", "class": "javafx.scene.control.TitledPane", "text": "Left Set"},
        {"class": "edu.mit.ll.ersa.common.dashboard.components.VideoPlayerFXMLController", "id": "VideoPlayer"},
        {"class": "javafx.scene.layout.VBox", "id": "centerVBox"},
    ]
    identity = {
        "mandatory": {"class": "javafx.scene.layout.Region"},
        "assistive": {
            "window": "ERSA Mosaic", "lineage": lineage,
            "sibling_count": 6, "sibling_index": index,
        },
    }
    return CapturedComponent(
        name="Region", role="parent", description=None, accessible_id=None,
        application="ERSA Mosaic", window="ERSA Mosaic",
        hierarchy=("GridPane", "VideoPlayerFXMLController#VideoPlayer", "VBox#centerVBox", "Region"),
        actions=(), bounds=(7, 145 + index * 73, 468, 72),
        state=ComponentState(present=True, visible=True, showing=True),
        backend_properties={
            "bridge_pid": 7079, "node_ref": node_ref,
            "accessible_role": "PARENT", "stable_ancestors": lineage,
            "sibling_count": 6, "sibling_index": index,
        },
        authored_strategy=ComponentStrategy("javafx", {"identification": identity}),
        framework="javafx", native_class="javafx.scene.layout.Region",
    )


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
    assert interaction.repository_match.component_id == "11111111-1111-4111-8111-111111111111"
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


def test_distinct_generic_javafx_siblings_do_not_collapse_into_one_interaction():
    session = RecordingSession()
    session.start()
    session.observe(PointerInteraction(
        1.0, "javafx", _javafx_region(0, node_ref="n19"),
        phase="released", coordinates=(252, 190),
    ))
    session.observe(PointerInteraction(
        1.1, "javafx", _javafx_region(1, node_ref="n1"),
        phase="released", coordinates=(239, 243),
    ))

    interactions = session.stop()

    assert len(interactions) == 2
    assert [item.target.backend_properties["sibling_index"] for item in interactions] == [0, 1]


def test_repository_matching_uses_javafx_lineage_and_sibling_evidence():
    first = _javafx_region(0, node_ref="persisted-0")
    second = _javafx_region(5, node_ref="persisted-5")
    repository = ComponentRepository({
        "first-region": ComponentDefinition(
            component_id="first-region", strategies=(first.candidate_strategy(),),
            object_type=first.semantic_type(), framework="javafx",
            native_class=first.native_class,
        ),
        "sixth-region": ComponentDefinition(
            component_id="sixth-region", strategies=(second.candidate_strategy(),),
            object_type=second.semantic_type(), framework="javafx",
            native_class=second.native_class,
        ),
    })
    target = _javafx_region(5, node_ref="current-run-ref")
    session = RecordingSession(repository=repository)
    session.start()
    session.observe(PointerInteraction(
        1.0, "javafx", target, phase="released", coordinates=(179, 448),
    ))

    interaction = session.stop()[0]

    assert interaction.repository_match.status == "known_unique"
    assert interaction.repository_match.component_id == repository.get("sixth-region").object_id


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


def test_verbose_debug_log_persists_runtime_observations_and_match_decisions(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOMATION_HARNESS_JAVA_AGENT_TOKEN", "do-not-write-this")
    monkeypatch.setenv("JAVA_TOOL_OPTIONS", "-javaagent:agent.jar=token=also-secret;port=9418")
    debug_log = RecordingDebugLog(tmp_path)
    session = RecordingSession(
        repository=_repository(), diagnostics=True, debug_log=debug_log,
    )
    session.start()
    session.observe(PointerInteraction(
        1.0, "javafx", _capture("Open"), phase="released", coordinates=(15, 15),
    ))
    session.stop()

    contents = debug_log.path.read_text(encoding="utf-8")
    assert "diagnostic_log_created" in contents
    assert "observation_received" in contents
    assert "repository_match" in contents
    assert "interaction_flushed" in contents
    assert "do-not-write-this" not in contents
    assert "also-secret" not in contents
    assert "<redacted>" in contents


def test_verbose_debug_log_omits_reflective_graphs_and_bounds_collections(tmp_path):
    debug_log = RecordingDebugLog(tmp_path)
    debug_log.write(
        "adapter.normalized_observation",
        target={
            "render_surface_inspection": {"backing_objects": [{"candidate_methods": list(range(500))}]},
            "track_instance_candidates": {"field-%03d" % index: index for index in range(150)},
        },
        repeated=list(range(100)),
    )

    contents = debug_log.path.read_text(encoding="utf-8")
    assert "<verbose reflective detail omitted>" in contents
    assert "36 sequence entries omitted" in contents
    assert "54 mapping entries omitted" in contents
    assert len(contents) < 30000


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


def _solipsys_track_capture(identity="2", *, bounds=(737, 480, 1, 1), runtime_ref="display-a"):
    identification = {
        "mandatory": {
            "rendered_class": "com.solipsys.tdf.track.DefaultTrackVelocityDisplay2D",
            "track_class": "com.solipsys.msct.track.report.MSCTTrackReport",
            "track_identity_key": "getTrackId",
            "track_identity_value": identity,
        },
        "assistive": {
            "accessible_id": "panel0",
            "native_class": "com.solipsys.view.AWTViewCanvas",
            "window": "MSCT Domain 12",
        },
    }
    return CapturedComponent(
        name=identity, role="rendered_object", description=None, accessible_id=None,
        application="MSCT Domain 12", window="MSCT Domain 12",
        hierarchy=("AWTViewCanvas", "DefaultTrackVelocityDisplay2D"),
        actions=("resolve", "click"), bounds=bounds,
        state=ComponentState(present=True, visible=True, showing=True),
        backend_properties={"ref": runtime_ref, "render_surface_adapter": "solipsys_awt_view_canvas"},
        authored_strategy=ComponentStrategy("java_agent", {"identification": identification}),
        object_type=ObjectType.CUSTOM, framework="solipsys_rendered",
        native_class="com.solipsys.tdf.track.DefaultTrackVelocityDisplay2D",
    )


def test_repository_matching_recognizes_same_solipsys_track_after_it_moves():
    persisted = _solipsys_track_capture()
    repository = ComponentRepository({
        "Track 2": ComponentDefinition(
            component_id="Track 2", strategies=(persisted.candidate_strategy(),),
            object_type=persisted.semantic_type(), framework="solipsys_rendered",
            native_class=persisted.native_class,
        ),
    })
    moved = _solipsys_track_capture(bounds=(1297, 227, 1, 1), runtime_ref="display-b")
    session = RecordingSession(repository=repository)
    session.start()
    session.observe(PointerInteraction(
        1.0, "solipsys_rendered", moved, phase="released", coordinates=(1297, 227),
    ))

    interaction = session.stop()[0]

    assert interaction.repository_match.status == "known_unique"
    assert interaction.repository_match.component_id == repository.get("Track 2").object_id


def test_repository_matching_rejects_different_solipsys_track_identity():
    persisted = _solipsys_track_capture("2")
    repository = ComponentRepository({
        "Track 2": ComponentDefinition(
            component_id="Track 2", strategies=(persisted.candidate_strategy(),),
            object_type=persisted.semantic_type(), framework="solipsys_rendered",
            native_class=persisted.native_class,
        ),
    })
    session = RecordingSession(repository=repository)
    session.start()
    session.observe(PointerInteraction(
        1.0, "solipsys_rendered", _solipsys_track_capture("3"),
        phase="released", coordinates=(900, 300),
    ))

    assert session.stop()[0].repository_match.status == "new_candidate"


def _runtime_only_solipsys_capture(track_ref, rendered_ref, bounds):
    return CapturedComponent(
        name="track@%s" % track_ref, role="rendered_object", description=None,
        accessible_id=None, application="MSCT Domain 12", window="MSCT Domain 12",
        hierarchy=("AWTViewCanvas", "DefaultTrackVelocityDisplay2D"),
        actions=("resolve", "click"), bounds=bounds,
        state=ComponentState(present=True, visible=True, showing=True),
        backend_properties={
            "track_runtime_ref": track_ref,
            "rendered_object_ref": rendered_ref,
            "identity_state": "runtime_only",
            "identity_rejection_reason": "no validated durable track identity",
        },
        authored_strategy=ComponentStrategy("java_agent", {
            "runtime_correlation": {
                "track_runtime_ref": track_ref,
                "rendered_object_ref": rendered_ref,
                "window": "MSCT Domain 12",
            }
        }),
        object_type=ObjectType.CUSTOM, framework="solipsys_rendered",
        native_class="com.solipsys.tdf.track.DefaultTrackVelocityDisplay2D",
    )


def test_runtime_track_reference_correlates_same_live_track_after_movement():
    session = RecordingSession()
    session.start()
    session.observe(PointerInteraction(
        1.0, "solipsys_rendered",
        _runtime_only_solipsys_capture("track-a", "display-a", (1128, 584, 13, 13)),
        phase="pressed", coordinates=(1134, 590),
    ))
    session.observe(PointerInteraction(
        1.1, "solipsys_rendered",
        _runtime_only_solipsys_capture("track-a", "display-a", (1185, 574, 13, 13)),
        phase="released", coordinates=(1191, 580),
    ))

    assert len(session.stop()) == 1


def test_runtime_correlation_rejects_distinct_tracks_that_share_bad_identity_value():
    session = RecordingSession()
    session.start()
    session.observe(PointerInteraction(
        1.0, "solipsys_rendered",
        _runtime_only_solipsys_capture("track-a", "display-a", (265, 295, 13, 13)),
        phase="released", coordinates=(271, 301),
    ))
    session.observe(PointerInteraction(
        2.0, "solipsys_rendered",
        _runtime_only_solipsys_capture("track-b", "display-b", (790, 655, 13, 13)),
        phase="released", coordinates=(796, 661),
    ))

    assert len(session.stop()) == 2


def test_recording_correlates_same_solipsys_identity_across_geometry_and_runtime_changes():
    session = RecordingSession()
    session.start()
    session.observe(PointerInteraction(
        1.0, "solipsys_rendered", _solipsys_track_capture(
            "2", bounds=(737, 480, 1, 1), runtime_ref="display-a",
        ), phase="pressed", coordinates=(737, 480),
    ))
    session.observe(PointerInteraction(
        2.0, "solipsys_rendered", _solipsys_track_capture(
            "2", bounds=(1297, 227, 1, 1), runtime_ref="display-b",
        ), phase="released", coordinates=(1297, 227),
    ))

    interactions = session.stop()

    assert len(interactions) == 1
    assert interactions[0].target.candidate_strategy().options["identification"]["mandatory"]["track_identity_value"] == "2"


def test_recording_does_not_correlate_distinct_solipsys_identities_at_same_geometry():
    session = RecordingSession()
    session.start()
    session.observe(PointerInteraction(
        1.0, "solipsys_rendered", _solipsys_track_capture("2"),
        phase="released", coordinates=(737, 480),
    ))
    session.observe(PointerInteraction(
        2.0, "solipsys_rendered", _solipsys_track_capture("3"),
        phase="released", coordinates=(737, 480),
    ))

    interactions = session.stop()

    assert len(interactions) == 2


def test_repository_matching_rejects_same_track_identity_in_different_explicit_scope():
    persisted = _solipsys_track_capture("2")
    repository = ComponentRepository({
        "Track 2": ComponentDefinition(
            component_id="Track 2", strategies=(persisted.candidate_strategy(),),
            object_type=persisted.semantic_type(), framework="solipsys_rendered",
            native_class=persisted.native_class,
        ),
    })
    different_scope = _solipsys_track_capture("2")
    identity = dict(different_scope.candidate_strategy().options["identification"])
    identity["assistive"] = dict(identity["assistive"], window="MSCT Domain 99")
    different_scope = replace(
        different_scope,
        authored_strategy=ComponentStrategy("java_agent", {"identification": identity}),
        application="MSCT Domain 99", window="MSCT Domain 99",
    )
    session = RecordingSession(repository=repository)
    session.start()
    session.observe(PointerInteraction(
        1.0, "solipsys_rendered", different_scope,
        phase="released", coordinates=(737, 480),
    ))

    assert session.stop()[0].repository_match.status == "new_candidate"
