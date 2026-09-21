from __future__ import annotations

import time

import pytest

from automation_harness.core.hybrid_object_capture import HybridObjectCaptureService
from automation_harness.models.component import CapturedComponent, ComponentState, ComponentStrategy
from automation_harness.models.gui import ObjectType


def _capture(strategy="atspi"):
    authored = None
    framework = None
    accessible_id = "follow"
    backend_properties = {}
    native_class = None
    if strategy == "javafx":
        authored = ComponentStrategy("javafx", {
            "identification": {
                "mandatory": {"id": "cameraSelectorButton"},
                "assistive": {"window": "ERSA Main Video Display"},
            }
        })
        framework = "javafx"
        accessible_id = "cameraSelectorButton"
        backend_properties = {"javafx_id": "cameraSelectorButton"}
        native_class = "javafx.scene.control.Button"
    return CapturedComponent(
        name="Select Camera" if strategy == "javafx" else "Follow",
        role="button" if strategy == "javafx" else "push button",
        description=None,
        accessible_id=accessible_id,
        application="ERSA Main Video Display" if strategy == "javafx" else "Reference",
        window="ERSA Main Video Display" if strategy == "javafx" else "Reference",
        hierarchy=(),
        actions=("activate", "click") if strategy == "javafx" else ("click",),
        bounds=(10, 20, 100, 30),
        state=ComponentState(present=True, visible=True, showing=True, enabled=True),
        backend_properties=backend_properties,
        authored_strategy=authored,
        framework=framework,
        native_class=native_class,
    )


class _Endpoint:
    pid = 12001


class _Stage:
    def __init__(self, source, criteria, matches):
        self.source = source
        self.criteria = criteria
        self.matches = matches


class _AtspiFailure:
    available = True

    def capture_next_click(self, *, timeout):
        raise LookupError("clicked accessibility event has no application-scoped component bounds")


class _AtspiSuccess:
    available = True

    def capture_next_click(self, *, timeout):
        return _capture("atspi")


class _JavaFxSuccess:
    available = True

    def endpoints(self):
        return (_Endpoint(),)

    def capture_next_click(self, *, timeout):
        time.sleep(0.02)
        return _capture("javafx")

    def assess_identification(self, identification, *, process_id=None):
        return (_Stage("mandatory", dict(identification["mandatory"]), 1),)


class _UnavailableJavaAgent:
    available = False


def _solipsys_capture(*, visible_matches=1):
    return CapturedComponent(
        name="2", role="rendered_object", description=None,
        accessible_id=None, application="MSCT", window="MSCT",
        hierarchy=("AWTViewCanvas", "DefaultTrackVelocityDisplay2D"),
        actions=("resolve", "click"), bounds=(100, 200, 13, 13),
        state=ComponentState(present=True, visible=True, showing=True),
        backend_properties={
            "identity_state": "candidate",
            "identity_visible_match_count": visible_matches,
            "identity_unique_in_visible_scope": visible_matches == 1,
        },
        authored_strategy=ComponentStrategy("java_agent", {"identification": {
            "mandatory": {
                "rendered_class": "com.solipsys.tdf.track.DefaultTrackVelocityDisplay2D",
                "track_class": "com.solipsys.msct.track.report.MSCTTrackReport",
                "track_identity_key": "getTrackId",
                "track_identity_value": "2",
            },
            "assistive": {
                "native_class": "com.solipsys.view.AWTViewCanvas",
                "window": "MSCT",
            },
        }}),
        object_type=ObjectType.CUSTOM, framework="solipsys_rendered",
        native_class="com.solipsys.tdf.track.DefaultTrackVelocityDisplay2D",
    )


def test_solipsys_definition_preserves_provisional_unique_identity_state():
    service = HybridObjectCaptureService(
        driver=_AtspiFailure(), javafx_driver=_JavaFxSuccess(),
        java_agent_driver=_UnavailableJavaAgent(),
    )

    definition = service.definition_from_capture(
        "msct.track.2", _solipsys_capture(), validate_live=False,
    )

    assert definition.framework == "solipsys_rendered"
    assert definition.properties["identity_state"] == "candidate"
    assert definition.properties["identity_visible_match_count"] == 1


def test_solipsys_definition_rejects_identity_duplicated_in_visible_scope():
    service = HybridObjectCaptureService(
        driver=_AtspiFailure(), javafx_driver=_JavaFxSuccess(),
        java_agent_driver=_UnavailableJavaAgent(),
    )

    with pytest.raises(ValueError, match="ambiguous in the visible surface scope"):
        service.definition_from_capture(
            "msct.track.2", _solipsys_capture(visible_matches=2), validate_live=False,
        )


def test_solipsys_definition_rejects_disqualified_field_identity():
    service = HybridObjectCaptureService(
        driver=_AtspiFailure(), javafx_driver=_JavaFxSuccess(),
        java_agent_driver=_UnavailableJavaAgent(),
    )
    capture = _solipsys_capture()
    identification = dict(capture.authored_strategy.options["identification"])
    identification["mandatory"] = dict(
        identification["mandatory"], track_identity_key="field:identity",
    )
    capture = CapturedComponent(**{
        **capture.__dict__,
        "authored_strategy": ComponentStrategy(
            "java_agent", {"identification": identification},
        ),
    })

    with pytest.raises(ValueError, match="no validated durable identity"):
        service.definition_from_capture(
            "msct.track.2", capture, validate_live=False,
        )


class _JavaFxFailure:
    available = True

    def endpoints(self):
        return (_Endpoint(),)

    def capture_next_click(self, *, timeout):
        time.sleep(0.05)
        raise TimeoutError("no JavaFX click")


def test_javafx_capture_survives_earlier_empty_atspi_frame_failure():
    service = HybridObjectCaptureService(driver=_AtspiFailure(), javafx_driver=_JavaFxSuccess())
    captured = service.capture_next_click(timeout=1.0)
    assert captured.framework == "javafx"
    assert captured.accessible_id == "cameraSelectorButton"


def test_native_atspi_capture_can_win_hybrid_race():
    service = HybridObjectCaptureService(driver=_AtspiSuccess(), javafx_driver=_JavaFxFailure())
    captured = service.capture_next_click(timeout=1.0)
    assert captured.framework is None
    assert captured.accessible_id == "follow"


def test_instrumented_javafx_wins_bounded_arbitration_after_atspi_success():
    service = HybridObjectCaptureService(driver=_AtspiSuccess(), javafx_driver=_JavaFxSuccess())
    captured = service.capture_next_click(timeout=1.0)
    assert captured.framework == "javafx"
    assert captured.accessible_id == "cameraSelectorButton"


def test_javafx_definition_preserves_native_identity_and_framework():
    service = HybridObjectCaptureService(driver=_AtspiFailure(), javafx_driver=_JavaFxSuccess())
    definition = service.definition_from_capture("mvd.camera_selector", _capture("javafx"))
    assert definition.framework == "javafx"
    assert definition.native_class == "javafx.scene.control.Button"
    assert definition.strategies[0].type == "javafx"
    identity = definition.strategies[0].options["identification"]
    assert identity["mandatory"] == {"id": "cameraSelectorButton"}
    assert identity["assistive"]["window"] == "ERSA Main Video Display"
    assert "activate" in definition.actions


def test_javafx_definition_validation_is_scoped_to_captured_process():
    class ProcessAware(_JavaFxSuccess):
        process_ids = []

        def assess_identification(self, identification, *, process_id=None):
            self.process_ids.append(process_id)
            return super().assess_identification(
                identification, process_id=process_id,
            )

    driver = ProcessAware()
    service = HybridObjectCaptureService(driver=_AtspiFailure(), javafx_driver=driver)
    captured = _capture("javafx")
    captured = CapturedComponent(
        **{
            **captured.__dict__,
            "backend_properties": {
                **dict(captured.backend_properties),
                "bridge_pid": 12001,
            },
        }
    )

    service.definition_from_capture("mvd.camera_selector", captured)

    assert driver.process_ids == [12001]


def test_recorded_javafx_definition_does_not_require_closed_transient_to_reappear():
    class ClosedTransient(_JavaFxSuccess):
        def assess_identification(self, identification, *, process_id=None):
            raise AssertionError("closed transient must not be queried during persistence")

    service = HybridObjectCaptureService(
        driver=_AtspiFailure(), javafx_driver=ClosedTransient(),
    )
    captured = _capture("javafx")

    definition = service.definition_from_capture(
        "mvd.file_menu", captured, validate_live=False,
    )

    assert definition.framework == "javafx"
    assert definition.strategies[0] == captured.candidate_strategy()


def test_javafx_definition_rejects_ambiguous_identity():
    class Ambiguous(_JavaFxSuccess):
        def assess_identification(self, identification, *, process_id=None):
            return (_Stage("mandatory", dict(identification["mandatory"]), 2),)

    service = HybridObjectCaptureService(driver=_AtspiFailure(), javafx_driver=Ambiguous())
    captured = _capture("javafx")
    captured = CapturedComponent(
        **{
            **captured.__dict__,
            "authored_strategy": ComponentStrategy("javafx", {
                "identification": {"mandatory": {"class": "javafx.scene.control.Button"}}
            }),
        }
    )
    import pytest
    with pytest.raises(ValueError, match="remains ambiguous"):
        service.definition_from_capture("mvd.button", captured)
