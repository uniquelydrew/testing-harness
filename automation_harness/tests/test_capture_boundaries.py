from automation_harness.core.capture_boundaries import (
    CaptureBoundaryKind,
    annotate_capture_boundary,
    classify_capture_boundary,
    surface_relative_visual_capture,
)
from automation_harness.models.component import CapturedComponent, ComponentState


def _capture(native_class, *, framework=None, properties=None):
    return CapturedComponent(
        name="surface", role="canvas", description=None,
        accessible_id="surface", application="Display", window="Display",
        hierarchy=("JFrame", "JPanel", "surface"), actions=("click",),
        bounds=(10, 20, 800, 600), state=ComponentState(present=True),
        backend_properties=properties or {}, framework=framework,
        native_class=native_class,
    )


def test_jogl_canvas_is_a_concrete_visual_boundary():
    boundary = classify_capture_boundary(_capture("com.jogamp.opengl.awt.GLCanvas"))
    assert boundary.kind is CaptureBoundaryKind.JOGL_SURFACE
    assert boundary.framework == "jogl"
    assert boundary.supports_native_children is False
    assert boundary.supports_visual_children is True


def test_legacy_jogl_package_is_recognized():
    boundary = classify_capture_boundary(_capture("javax.media.opengl.awt.GLJPanel"))
    assert boundary.kind is CaptureBoundaryKind.JOGL_SURFACE


def test_opaque_swing_failure_is_not_misclassified_as_javafx():
    boundary = classify_capture_boundary(_capture("com.msct.display.TacticalCanvas", framework="swing"))
    assert boundary.kind is CaptureBoundaryKind.SWING
    assert boundary.framework != "javafx"


def test_javafx_requires_positive_class_or_framework_evidence():
    unknown = classify_capture_boundary(_capture("com.msct.display.UnknownSurface"))
    actual = classify_capture_boundary(_capture("javafx.embed.swing.JFXPanel"))
    assert unknown.kind is CaptureBoundaryKind.UNKNOWN
    assert actual.kind is CaptureBoundaryKind.JAVA_FX


def test_boundary_annotation_is_persistable_capture_evidence():
    captured = annotate_capture_boundary(_capture("com.jogamp.opengl.awt.GLJPanel"))
    assert captured.framework == "jogl"
    assert captured.backend_properties["capture_boundary"]["kind"] == "jogl_surface"


def test_jogl_click_can_become_normalized_surface_relative_visual_leaf():
    captured = annotate_capture_boundary(_capture(
        "com.jogamp.opengl.awt.GLCanvas",
        properties={"capture_point": [410, 320]},
    ))

    visual = surface_relative_visual_capture(captured, region_size=40)

    strategy = visual.candidate_strategy()
    assert strategy.type == "anchored_visual"
    assert visual.framework == "visual"
    assert visual.bounds == (390, 300, 40, 40)
    assert all(0 <= value <= 1 for value in strategy.options["relative_bounds"])
