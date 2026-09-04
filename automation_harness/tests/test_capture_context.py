from automation_harness.authoring.capture_context import (
    CaptureContext,
    CaptureContextNode,
    build_recording_context,
    _resolved_semantic_ref,
    _semantic_children,
    identity_descriptors,
    is_semantic_node,
    is_structural_context_node,
    node_label,
    suggested_name,
    build_capture_context,
)


def _node(ref, class_name, **values):
    payload = {
        "ref": ref,
        "class": class_name,
        "simple_class": class_name.rsplit(".", 1)[-1],
        "id": None,
        "accessible_role": "PARENT",
        "accessible_text": None,
        "text": None,
        "layout": {},
        "properties": {},
        "children": [],
    }
    payload.update(values)
    return payload


def _javafx_capture():
    from automation_harness.models.component import CapturedComponent, ComponentState

    return CapturedComponent(
        name="File", role="menu", description=None, accessible_id="fileMenu",
        application="Demo", window="Main", hierarchy=("Main", "MenuBar", "File"),
        actions=("activate",), bounds=(10, 20, 80, 24),
        state=ComponentState(present=True), framework="javafx",
        native_class="javafx.scene.control.Menu",
        backend_properties={
            "node_ref": "node-7", "bridge_pid": 123, "bridge_port": 4567,
            "style_classes": ["menu"],
        },
    )


def test_javafx_capture_remains_usable_when_bridge_endpoint_disappears():
    class MissingDriver:
        def endpoints(self):
            return ()

    captured = _javafx_capture()
    context = build_capture_context(captured, MissingDriver())

    assert context.find(context.target_key).payload["node_ref"] == "node-7"
    assert context.captured_component(context.target_key) is captured


def test_javafx_capture_remains_usable_when_live_node_disappears():
    class Endpoint:
        pid = 123
        port = 4567

        def request(self, *args, **kwargs):
            return {"windows": []}

    class Driver:
        def endpoints(self):
            return (Endpoint(),)

    captured = _javafx_capture()
    context = build_capture_context(captured, Driver())

    assert context.captured_component(context.target_key) is captured


def test_semantic_tree_collapses_redundant_javafx_containers():
    target = _node(
        "target",
        "edu.mit.ll.ersa.FeedPanel",
        layout={"grid_row": 1, "grid_column": 2},
    )
    stack = _node("stack", "javafx.scene.layout.StackPane", children=[target])
    hbox = _node("hbox", "javafx.scene.layout.HBox", children=[stack])
    grid = _node("grid", "javafx.scene.layout.GridPane", id="videoGrid", children=[hbox])
    root = _node("root", "javafx.scene.layout.AnchorPane", id="AnchorPane", children=[grid])

    children = _semantic_children(root, "target")

    assert [item.key for item in children] == ["grid"]
    assert children[0].is_semantic is False
    assert [item.key for item in children[0].children] == ["target"]
    assert children[0].children[0].is_target is True


def test_application_classes_are_not_implicitly_interaction_boundaries():
    node = _node("feed", "edu.mit.ll.ersa.common.dashboard.components.VideoPlayerFXMLController")
    assert is_semantic_node(node) is False


def test_stable_application_container_is_structural_context_only():
    node = _node("feed", "edu.mit.ll.ersa.FeedPanel", id="primaryFeed")
    assert is_structural_context_node(node) is True
    assert is_semantic_node(node) is False


def test_javafx_skin_nodes_collapse_under_their_semantic_menu_owner():
    image = _node("image", "javafx.scene.image.ImageView")
    label = _node("label", "com.sun.javafx.scene.control.LabeledText", text="System")
    skin = _node("skin", "com.sun.javafx.scene.control.skin.MenuBarButton", children=[label, image])
    menu = _node(
        "menu", "javafx.scene.control.Menu", id="systemMenu",
        accessible_role="MENU", text="System", children=[skin],
    )

    children = _semantic_children(_node("root", "javafx.scene.layout.Pane", children=[menu]), "menu")

    assert [item.key for item in children] == ["menu"]
    assert children[0].is_semantic is True
    assert children[0].children == []


def test_physical_text_target_resolves_to_its_semantic_button_owner():
    text = _node("text", "com.sun.javafx.scene.control.LabeledText", text="2")
    button = _node(
        "button", "javafx.scene.control.Button", id="digitTwo",
        accessible_role="BUTTON", children=[text],
    )
    root = _node("root", "javafx.scene.layout.Pane", children=[button])

    assert _resolved_semantic_ref(root, "text") == "button"


def test_text_label_is_not_retained_beneath_semantic_button():
    label = _node(
        "label", "javafx.scene.control.Label",
        accessible_role="TEXT", text="Save",
    )
    button = _node(
        "button", "javafx.scene.control.Button", id="saveButton",
        accessible_role="BUTTON", children=[label],
    )

    children = _semantic_children(
        _node("root", "javafx.scene.layout.Pane", children=[button]), "button",
    )

    assert [item.key for item in children] == ["button"]
    assert children[0].children == []


def test_recording_context_checks_each_distinct_interacted_target():
    from automation_harness.models.component import CapturedComponent, ComponentState

    def captured(name, window, ref):
        return CapturedComponent(
            name=name, role="button", description=None, accessible_id=None,
            application="Demo", window=window, hierarchy=(window, name),
            actions=("activate",), bounds=(1, 2, 3, 4),
            state=ComponentState(present=True), framework="javafx",
            native_class="javafx.scene.control.Button",
            backend_properties={"node_ref": ref},
        )

    first = captured("Open", "Main", "a")
    duplicate = captured("Open", "Main", "a")
    second = captured("Confirm", "Dialog", "b")

    context = build_recording_context((first, duplicate, second))

    assert len(context.target_keys) == 2
    assert [child.label for child in context.root.children] == ["Main", "Dialog"]
    assert context.captured_component(context.target_keys[0]) is first


def test_recording_context_omits_raw_accessibility_ancestry():
    from automation_harness.models.component import CapturedComponent, ComponentState

    target = CapturedComponent(
        name="2", role="push button", description=None, accessible_id="digit-two",
        application="Calculator", window="Calculator",
        hierarchy=("Calculator", "panel", "panel", "label", "2"),
        actions=("click",), bounds=(10, 10, 20, 20),
        state=ComponentState(present=True), framework="atspi",
    )

    context = build_recording_context((target,))

    assert [node.label for node in context.root.walk()] == [
        "Recorded interaction scope", "Calculator", "2",
    ]
    assert context.root.children[0].children[0].is_target


def test_recording_context_deduplicates_repeated_semantic_target_observations():
    from dataclasses import replace
    from automation_harness.models.component import CapturedComponent, ComponentState

    first = CapturedComponent(
        name="2", role="push button", description=None, accessible_id="digit-two",
        application="Calculator", window="Calculator",
        hierarchy=("Calculator", "panel", "2"), actions=("click",),
        bounds=(10, 10, 20, 20), state=ComponentState(present=True), framework="atspi",
    )
    later = replace(
        first, hierarchy=("Desktop", "Calculator", "panel", "panel", "2"),
        bounds=(11, 10, 20, 20),
    )

    context = build_recording_context((first, later))

    assert len(context.target_keys) == 1
    assert context.captured_component(context.target_key) is first


def test_numeric_accessible_id_does_not_replace_semantic_display_name():
    payload = {
        "id": "2",
        "accessible_text": "03",
        "accessible_role": "push button",
    }

    assert node_label(payload) == "03"
    assert suggested_name(payload) == "03"


def test_descriptive_accessible_id_remains_preferred_authored_name():
    payload = {
        "id": "save-button",
        "accessible_text": "Save",
        "accessible_role": "push button",
    }

    assert node_label(payload) == "Save"
    assert suggested_name(payload) == "save-button"


def test_recording_peer_groups_do_not_mix_unrelated_semantic_roles():
    button = CaptureContextNode(
        "button", "Save", {"accessible_role": "push button"}, is_target=True,
    )
    entry = CaptureContextNode(
        "entry", "Name", {"accessible_role": "text"}, is_target=True,
    )
    root = CaptureContextNode(
        "window", "Dialog", {"window": "Dialog"}, [button, entry], is_window_root=True,
    )
    context = CaptureContext("atspi", root, "button")

    assert context.selected_group("button") == [button]


def test_common_peer_descriptors_identify_shared_group_properties():
    first = CaptureContextNode(
        "a",
        "Feed A",
        {
            "class": "edu.mit.ll.ersa.FeedPanel",
            "accessible_role": "PARENT",
            "layout": {"grid_row": 0, "grid_column": 0},
            "properties": {},
        },
    )
    second = CaptureContextNode(
        "b",
        "Feed B",
        {
            "class": "edu.mit.ll.ersa.FeedPanel",
            "accessible_role": "PARENT",
            "layout": {"grid_row": 0, "grid_column": 1},
            "properties": {},
        },
    )
    root = CaptureContextNode("root", "MVD", {"window": "MVD"}, [first, second], is_window_root=True)
    context = CaptureContext("javafx", root, "a")

    common = context.common_peer_descriptors("a")

    assert common["class"] == "edu.mit.ll.ersa.FeedPanel"
    assert common["accessible_role"] == "PARENT"
    assert "layout.grid_column" not in common


def test_inherited_descriptors_distinguish_parent_from_older_ancestors():
    target = CaptureContextNode("target", "Feed", {"class": "edu.mit.ll.ersa.FeedPanel"}, is_target=True)
    parent = CaptureContextNode(
        "grid",
        "Video Grid",
        {"id": "videoGrid", "class": "javafx.scene.layout.GridPane"},
        [target],
    )
    ancestor = CaptureContextNode(
        "content",
        "Content",
        {"id": "contentPane", "class": "javafx.scene.layout.AnchorPane"},
        [parent],
    )
    root = CaptureContextNode("root", "MVD", {"window": "MVD"}, [ancestor], is_window_root=True)
    context = CaptureContext("javafx", root, "target")

    inherited = context.inherited_descriptors("target")

    assert inherited["window"] == "MVD"
    assert inherited["parent.id"] == "videoGrid"
    assert inherited["parent.class"] == "javafx.scene.layout.GridPane"
    assert inherited["ancestor[0].id"] == "contentPane"


def test_domain_properties_are_exposed_as_identity_descriptors():
    node = {
        "class": "edu.mit.ll.ersa.FeedPanel",
        "properties": {
            "automation.feed-id": "Camera12",
            "javafx.css.pseudoClassState": "ignored",
        },
        "layout": {"grid_row": 1, "grid_column": 2},
    }

    values = identity_descriptors(node)

    assert values["properties.automation.feed-id"] == "Camera12"
    assert "properties.javafx.css.pseudoClassState" not in values
    assert values["layout.grid_row"] == 1
    assert values["layout.grid_column"] == 2
