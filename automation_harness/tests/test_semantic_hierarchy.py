from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.semantic_hierarchy import (
    materialize_semantic_ancestors,
    semantic_ancestor_descriptors,
)
from automation_harness.models.component import (
    CapturedComponent,
    ComponentState,
    ComponentStrategy,
)
from automation_harness.models.gui import ObjectType


def _capture(framework, ancestors, *, window="MVD"):
    return CapturedComponent(
        name="Username",
        role="text field",
        description=None,
        accessible_id="username",
        application="MVD",
        window=window,
        hierarchy=(),
        actions=("click", "focus", "set_text"),
        bounds=(100, 200, 160, 24),
        state=ComponentState(present=True, visible=True, showing=True),
        backend_properties={"semantic_ancestors": ancestors},
        authored_strategy=ComponentStrategy(
            "java_agent" if framework == "swing" else "atspi",
            {"identification": {"mandatory": {"accessible_id": "username"}}},
        ),
        object_type=ObjectType.TEXT_FIELD,
        framework=framework,
        native_class="javax.swing.JTextField" if framework == "swing" else None,
    )


def test_semantic_hierarchy_discards_structural_swing_panels():
    capture = _capture("swing", [
        {
            "framework": "swing",
            "object_type": "window",
            "name": "MSCT",
            "native_class": "javax.swing.JFrame",
            "component_path": "JFrame[0]",
            "window": "MSCT",
        },
        {
            "framework": "swing",
            "object_type": "panel",
            "native_class": "javax.swing.JPanel",
            "component_path": "JFrame[0]/JPanel[0]",
            "window": "MSCT",
        },
        {
            "framework": "swing",
            "object_type": "tab_container",
            "name": "Main Tabs",
            "accessible_id": "mainTabs",
            "native_class": "javax.swing.JTabbedPane",
            "component_path": "JFrame[0]/JPanel[0]/JTabbedPane[0]",
            "window": "MSCT",
        },
    ], window="MSCT")

    descriptors = semantic_ancestor_descriptors(capture)

    assert [item["object_type"] for item in descriptors] == [
        "window",
        "tab_container",
    ]
    assert [item.get("name") for item in descriptors] == [
        "MSCT",
        "Main Tabs",
    ]


def test_materialized_semantic_ancestors_form_real_owner_chain():
    capture = _capture("swing", [
        {
            "framework": "swing",
            "object_type": "window",
            "name": "MSCT",
            "native_class": "javax.swing.JFrame",
            "component_path": "JFrame[0]",
            "window": "MSCT",
        },
        {
            "framework": "swing",
            "object_type": "tab_container",
            "name": "Main Tabs",
            "accessible_id": "mainTabs",
            "native_class": "javax.swing.JTabbedPane",
            "component_path": "JFrame[0]/JTabbedPane[0]",
            "window": "MSCT",
        },
    ], window="MSCT")

    repository, owner_id, created = materialize_semantic_ancestors(
        ComponentRepository({}), capture,
    )

    assert created == ("MSCT Window", "Main Tabs")
    window = repository.get("MSCT Window")
    tabs = repository.get("Main Tabs")
    assert tabs.owner_object_id == window.object_id
    assert owner_id == tabs.object_id
    assert window.properties["semantic_container"] is True
    assert tabs.properties["semantic_container"] is True


def test_repeated_materialization_reuses_same_semantic_ancestors():
    capture = _capture("swing", [
        {
            "framework": "swing",
            "object_type": "window",
            "name": "MSCT",
            "native_class": "javax.swing.JFrame",
            "component_path": "JFrame[0]",
            "window": "MSCT",
        },
        {
            "framework": "swing",
            "object_type": "tab_container",
            "name": "Main Tabs",
            "accessible_id": "mainTabs",
            "native_class": "javax.swing.JTabbedPane",
            "component_path": "JFrame[0]/JTabbedPane[0]",
            "window": "MSCT",
        },
    ], window="MSCT")

    repository, owner_id, _created = materialize_semantic_ancestors(
        ComponentRepository({}), capture,
    )
    repository, repeated_owner_id, repeated_created = materialize_semantic_ancestors(
        repository, capture,
    )

    assert repeated_created == ()
    assert repeated_owner_id == owner_id
    assert len(repository.components) == 2


def test_javafx_outer_scene_root_becomes_resolvable_window_proxy():
    capture = CapturedComponent(
        name="Username",
        role="TEXT_FIELD",
        description=None,
        accessible_id="username",
        application="MVD",
        window="MVD",
        hierarchy=(),
        actions=("click", "focus", "set_text"),
        bounds=(100, 200, 160, 24),
        state=ComponentState(present=True, visible=True, showing=True),
        backend_properties={
            "stable_ancestors": [
                {"class": "javafx.scene.layout.GridPane"},
                {
                    "id": "mainTabs",
                    "class": "javafx.scene.control.TabPane",
                    "accessible_role": "PARENT",
                },
            ],
        },
        authored_strategy=ComponentStrategy(
            "javafx",
            {"identification": {"mandatory": {"id": "username"}}},
        ),
        object_type=ObjectType.TEXT_FIELD,
        framework="javafx",
        native_class="javafx.scene.control.TextField",
    )

    repository, owner_id, created = materialize_semantic_ancestors(
        ComponentRepository({}), capture,
    )

    assert created == ("MVD Window", "Main Tabs")
    window = repository.get("MVD Window")
    tabs = repository.get("Main Tabs")
    assert window.properties["window_proxy"] is True
    assert tabs.owner_object_id == window.object_id
    assert owner_id == tabs.object_id
