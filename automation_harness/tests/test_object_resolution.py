import pytest

from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.component_handle import ComponentHandle
from automation_harness.core.object_resolution import ObjectResolutionError, resolve_repository_object
from automation_harness.models.component import CapturedComponent, ComponentDefinition, ComponentState, ComponentStrategy, ResolvedComponent


class _CaptureService:
    def capture_by_locator(self, *, identification):
        return CapturedComponent(
            name="surface", role="canvas", description=None,
            accessible_id="surface", application="Display", window="Display",
            hierarchy=("Display", "surface"), actions=(), bounds=(100, 200, 800, 600),
            state=ComponentState(present=True), native_class="com.jogamp.opengl.awt.GLCanvas",
        )


def _repository(status="ready"):
    surface = ComponentDefinition(
        component_id="Surface", framework="jogl",
        strategies=(ComponentStrategy("atspi", {
            "identification": {"mandatory": {"accessible_id": "surface"}},
        }),),
    )
    visual = ComponentDefinition(
        component_id="Surface.Track", owner_object_id=surface.object_id,
        strategies=(ComponentStrategy("anchored_visual", {
            "anchor_identification": {"mandatory": {"accessible_id": "surface"}},
            "relative_bounds": [0.25, 0.5, 0.1, 0.2],
        }),),
        properties={"locator_status": status},
    )
    return ComponentRepository({surface.component_id: surface, visual.component_id: visual}), visual


def test_visual_resolution_uses_current_concrete_owner_bounds():
    repository, visual = _repository()

    result = resolve_repository_object(_CaptureService(), repository, visual)

    assert result.strategy == "anchored_visual"
    assert result.bounds == (300, 500, 80, 120)
    assert result.owner.definition.component_id == "Surface"


def test_visual_resolution_rejects_locator_marked_for_recapture():
    repository, visual = _repository(status="needs_recapture")
    with pytest.raises(ObjectResolutionError, match="requires recapture"):
        resolve_repository_object(_CaptureService(), repository, visual)


def test_resolution_rejects_provisional_locator_before_driver_dispatch():
    repository, visual = _repository(status="provisional")
    with pytest.raises(ObjectResolutionError, match="provisional"):
        resolve_repository_object(_CaptureService(), repository, visual)


def test_runtime_playback_resolves_visual_from_owner_identity(monkeypatch):
    repository, visual = _repository()

    class Context:
        components = repository

    monkeypatch.setattr(
        ComponentHandle,
        "resolve",
        lambda self: ResolvedComponent(
            self.definition.component_id, "atspi", {"bounds": [100, 200, 800, 600]},
        ),
    )
    strategy = visual.strategies[0]

    result = ComponentHandle(Context(), visual)._resolve_strategy(strategy.type, strategy.options)

    assert result.metadata["bounds"] == [300, 500, 80, 120]
    assert result.metadata["owner_object_id"] == visual.owner_object_id


def test_resolved_existence_is_independent_of_visibility():
    from automation_harness.core.object_resolution import ObjectResolution
    from automation_harness.models.component import ComponentDefinition, CapturedComponent, ComponentState
    resolved = ObjectResolution(
        definition=ComponentDefinition(component_id="Main"),
        bounds=(0, 0, 100, 100),
        strategy="atspi",
        captured=CapturedComponent(
            name="Main", role="frame", description=None, accessible_id=None,
            application="Demo", hierarchy=(), actions=(), bounds=(0, 0, 100, 100),
            state=ComponentState(present=True, visible=False, showing=False),
        ),
    )
    assert resolved.observed_state("exists") is True
    assert resolved.observed_state("visible") is False
    assert resolved.observed_state("focused") is None


def test_native_identity_can_resolve_without_visual_geometry(monkeypatch):
    from automation_harness.core.object_resolution import ObjectResolution, resolve_repository_object
    from automation_harness.models.gui import ObjectType
    import automation_harness.core.object_resolution as module
    captured = CapturedComponent(
        name="Search", role="text field", description=None, accessible_id="search",
        application="Demo", hierarchy=(), actions=("set_text",),
        bounds=None, state=ComponentState(present=True), object_type=ObjectType.TEXT_FIELD,
    )
    monkeypatch.setattr(module, "resolve_live_capture", lambda *args, **kwargs: captured)
    definition = ComponentDefinition(
        "Search", object_type=ObjectType.TEXT_FIELD,
        strategies=(ComponentStrategy("atspi", {"identification": {"mandatory": {"accessible_id": "search"}}}),),
    )
    result = resolve_repository_object(None, ComponentRepository({"Search": definition}), definition)
    assert result.exists
    assert result.bounds is None
    assert result.has_visual_bounds is False
    from automation_harness.core.visual_projection import VisualProjectionError, project_visual_target
    with pytest.raises(VisualProjectionError, match="no verified visual"):
        project_visual_target(result, operation="highlight")


def test_native_semantic_type_mismatch_is_rejected(monkeypatch):
    from automation_harness.models.gui import ObjectType
    import automation_harness.core.object_resolution as module
    monkeypatch.setattr(module, "resolve_live_capture", lambda *args, **kwargs: CapturedComponent(
        name="Label", role="label", description=None, accessible_id="label",
        application="Demo", hierarchy=(), actions=(), bounds=(10, 20, 20, 10),
        state=ComponentState(present=True),
    ))
    definition = ComponentDefinition("ExpectedButton", object_type=ObjectType.BUTTON,
        strategies=(ComponentStrategy("atspi", {"identification": {"mandatory": {"accessible_id": "label"}}}),))
    with pytest.raises(ObjectResolutionError, match="incompatible semantic type"):
        resolve_repository_object(None, ComponentRepository({"ExpectedButton": definition}), definition)


def test_verified_visual_projection_uses_resolved_bounds():
    from automation_harness.core.object_resolution import ObjectResolution
    from automation_harness.core.visual_projection import project_visual_target
    result = ObjectResolution(definition=ComponentDefinition("Button"), bounds=(1, 2, 30, 40), strategy="atspi")
    visual = project_visual_target(result, operation="highlight")
    assert visual.bounds == (1, 2, 30, 40)
    assert visual.semantic is result


@pytest.mark.parametrize("framework,native_class,role,expected", [
    ("javafx", "javafx.scene.control.MenuButton", "push button", "menu"),
    ("javafx", "javafx.scene.control.Menu", "menu", "menu"),
    ("javafx", "javafx.scene.control.ChoiceBox", "button", "combo_box"),
    ("javafx", "javafx.scene.control.ComboBox", "combo box", "combo_box"),
    ("swing", "javax.swing.JMenu", "menu", "menu"),
    ("swing", "javax.swing.JMenuBar", "unknown", "menu_bar"),
    ("swing", "javax.swing.JMenuItem", "unknown", "menu_item"),
    ("swing", "javax.swing.JComboBox", "unknown", "combo_box"),
    ("swing", "javax.swing.JToggleButton", "unknown", "toggle_button"),
    ("atspi", "GtkEntry", "entry", "text_field"),
    ("atspi", "GtkCheckMenuItem", "check menu item", "check_menu_item"),
    ("atspi", "GtkTextView", "text area", "text_area"),
])
def test_framework_native_identity_resolves_to_expected_semantic_type(
        monkeypatch, framework, native_class, role, expected):
    from automation_harness.models.gui import ObjectType
    import automation_harness.core.object_resolution as module
    object_type = ObjectType(expected)
    captured = CapturedComponent(
        name="Target", role=role, description=None, accessible_id="target",
        application="AUT", hierarchy=(), actions=("click",),
        bounds=(1, 2, 30, 40), state=ComponentState(present=True),
        native_class=native_class, framework=framework,
    )
    assert captured.semantic_type() == object_type
    monkeypatch.setattr(module, "resolve_live_capture", lambda *args, **kwargs: captured)
    definition = ComponentDefinition(
        "Target", object_type=object_type,
        strategies=(ComponentStrategy(framework if framework != "swing" else "java_agent",
            {"identification": {"mandatory": {"accessible_id": "target"}}}),),
    )
    assert resolve_repository_object(None,
        ComponentRepository({"Target": definition}), definition).exists


@pytest.mark.parametrize("expected,actual,allowed", [
    ("menu_item", "menu_item", True),
    ("menu_item", "check_menu_item", True),
    ("menu_item", "radio_menu_item", True),
    ("check_menu_item", "menu_item", False),
    ("menu", "menu_item", False),
    ("button", "toggle_button", False),
    ("combo_box", "menu", False),
    ("text_field", "label", False),
    ("text_field", "text_area", False),
    ("window", "dialog", True),
])
def test_semantic_family_allowlist_remains_fail_closed(expected, actual, allowed):
    from automation_harness.core.object_resolution import semantic_type_compatible
    from automation_harness.models.gui import ObjectType
    assert semantic_type_compatible(ObjectType(expected), ObjectType(actual)) is allowed
