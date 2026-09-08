from automation_harness.authoring.capture_context import (
    CaptureContext,
    CaptureContextNode,
    _semantic_children,
    identity_descriptors,
    is_semantic_node,
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
    assert [item.key for item in children[0].children] == ["target"]
    assert children[0].children[0].is_target is True


def test_application_classes_are_semantic_without_ids():
    node = _node("feed", "edu.mit.ll.ersa.common.dashboard.components.VideoPlayerFXMLController")
    assert is_semantic_node(node) is True


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
