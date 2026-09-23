from automation_harness.core.runtime_observation import Framework, RuntimeObservation
from automation_harness.models.component import CapturedComponent, ComponentState, ComponentStrategy


def test_capture_conversion_preserves_runtime_evidence_without_creating_identity():
    captured = CapturedComponent(
        name="File", role="menu", description="Application menu",
        accessible_id="fileMenu", application="MSCT", window="Main",
        hierarchy=("Main Window", "Menu Bar"), actions=("activate",),
        bounds=(10, 20, 40, 20),
        state=ComponentState(present=True, visible=True, enabled=True),
        backend_properties={"bridge_pid": 7123, "capture_point": [15, 25]},
        authored_strategy=ComponentStrategy("javafx", {
            "identification": {"mandatory": {"id": "fileMenu"}},
        }),
        framework="javafx", native_class="javafx.scene.control.Menu",
        logical_subobjects={"open": {"criteria": {"id": "openItem"}}},
    )

    observation = RuntimeObservation.from_capture(
        captured, adapter="javafx", authoritative=True,
        reasons=("owning PID exposes JavaFX endpoint",),
    )

    assert observation.framework is Framework.JAVAFX
    assert observation.process_id == 7123
    assert observation.window_id == "Main"
    assert observation.physical_class == "javafx.scene.control.Menu"
    assert [item.name for item in observation.physical_ancestry] == [
        "Main Window", "Menu Bar",
    ]
    assert observation.logical_children["open"]["criteria"]["id"] == "openItem"
    assert observation.evidence.authoritative is True


def test_adapter_does_not_override_explicit_rendered_framework():
    captured = CapturedComponent(
        name="track", role="rendered_object", description=None,
        accessible_id=None, application="MSCT", window="Scope",
        hierarchy=(), actions=("click",), bounds=(1, 2, 3, 4),
        state=ComponentState(present=True), framework="solipsys_rendered",
        native_class="com.solipsys.Track",
    )
    observation = RuntimeObservation.from_capture(
        captured, adapter="java_agent", authoritative=True,
    )
    assert observation.framework is Framework.RENDERED
