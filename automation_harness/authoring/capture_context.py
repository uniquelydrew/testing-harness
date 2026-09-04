from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from automation_harness.drivers.javafx_bridge import JavaFxBridgeDriver, JavaFxBridgeEndpoint, _captured


@dataclass
class CaptureContextNode:
    key: str
    label: str
    payload: dict[str, Any]
    children: list["CaptureContextNode"] = field(default_factory=list)
    is_target: bool = False
    is_window_root: bool = False
    is_semantic: bool = True

    def walk(self):
        yield self
        for child in self.children:
            for item in child.walk():
                yield item


@dataclass
class CaptureContext:
    framework: str
    root: CaptureContextNode
    target_key: str
    endpoint: JavaFxBridgeEndpoint | None = None
    target_keys: tuple[str, ...] = ()
    captured_by_key: Mapping[str, Any] = field(default_factory=dict)

    def find(self, key: str) -> CaptureContextNode | None:
        for node in self.root.walk():
            if node.key == key:
                return node
        return None

    def parent_of(self, key: str) -> CaptureContextNode | None:
        def visit(parent):
            for child in parent.children:
                if child.key == key:
                    return parent
                found = visit(child)
                if found is not None:
                    return found
            return None
        return visit(self.root)

    def siblings_of(self, key: str) -> list[CaptureContextNode]:
        parent = self.parent_of(key)
        if parent is None:
            return []
        return [child for child in parent.children if child.key != key]

    def selected_group(self, key: str) -> list[CaptureContextNode]:
        node = self.find(key)
        if node is None:
            return []
        parent = self.parent_of(key)
        if parent is None:
            return [node]
        class_name = node.payload.get("class")
        if class_name:
            peers = [child for child in parent.children if child.payload.get("class") == class_name]
            if peers:
                return peers
        return list(parent.children)

    def inherited_descriptors(self, key: str) -> dict[str, Any]:
        result = {}
        path = self.path_to(key)
        if not path:
            return result
        root = path[0]
        window = root.payload.get("window")
        if window:
            result["window"] = window

        ancestors = path[1:-1]
        if ancestors:
            parent = ancestors[-1]
            for prop, value in stable_descriptor(parent.payload).items():
                result["parent.%s" % prop] = value
            for index, ancestor in enumerate(ancestors[:-1]):
                for prop, value in stable_descriptor(ancestor.payload).items():
                    result["ancestor[%s].%s" % (index, prop)] = value
        return result

    def common_peer_descriptors(self, key: str) -> dict[str, Any]:
        peers = self.selected_group(key)
        if len(peers) < 2:
            return {}
        rows = [identity_descriptors(peer.payload) for peer in peers]
        common = dict(rows[0])
        for prop in list(common):
            expected = common[prop]
            if any(prop not in row or row[prop] != expected for row in rows[1:]):
                common.pop(prop, None)
        return common

    def path_to(self, key: str) -> list[CaptureContextNode]:
        path = []

        def visit(node):
            path.append(node)
            if node.key == key:
                return True
            for child in node.children:
                if visit(child):
                    return True
            path.pop()
            return False

        return list(path) if visit(self.root) else []

    def captured_component(self, key: str):
        captured = self.captured_by_key.get(key)
        if captured is not None:
            return captured
        node = self.find(key)
        if node is None:
            raise KeyError(key)
        if not node.is_semantic:
            raise ValueError("structural context cannot be captured as an object")
        if self.framework != "javafx" or self.endpoint is None:
            raise ValueError("context node cannot be converted to a live captured component")
        return _captured(self.endpoint, node.payload)


def build_capture_context(captured, javafx_driver=None, max_depth=64):
    """Build a window-rooted semantic tree around a captured object.

    JavaFX capture loads the live window tree and collapses implementation-only
    containers. The captured node, stable application nodes, controls, and
    semantically named nodes remain. Non-JavaFX backends fall back to the
    hierarchy already present on CapturedComponent.
    """
    framework = str(getattr(captured, "framework", "") or "")
    if framework == "javafx":
        return _build_javafx_context(captured, javafx_driver or JavaFxBridgeDriver(), max_depth=max_depth)
    return _build_fallback_context(captured)


def build_recording_context(captures):
    """Build a compact checked workbench scope from recorded semantic targets.

    A recorded capture's raw accessibility ancestry is diagnostic data, not an
    authoring tree.  Replaying it here exposed toolkit skin nodes (labels,
    fillers and panels) and repeated that ancestry for every observation.
    Recording review therefore groups durable semantic targets by window only.
    """
    distinct = []
    seen = set()
    for captured in captures:
        if captured is None:
            continue
        identity = _recorded_capture_identity(captured)
        if identity in seen:
            continue
        seen.add(identity)
        distinct.append(captured)
    if not distinct:
        raise ValueError("recording contains no semantic targets")

    root = CaptureContextNode(
        key="recording-root", label="Recorded interaction scope",
        payload={}, is_window_root=True, is_semantic=False,
    )
    windows = {}
    captured_by_key = {}
    target_keys = []
    for index, captured in enumerate(distinct):
        window_name = str(
            getattr(captured, "window", None)
            or getattr(captured, "application", None)
            or "Application Window"
        )
        window_key = "recording-window-%d" % len(windows)
        window = windows.get(window_name)
        if window is None:
            window = CaptureContextNode(
                key=window_key, label=window_name, payload={"window": window_name},
                is_window_root=True, is_semantic=False,
            )
            windows[window_name] = window
            root.children.append(window)

        target_key = "recording-target-%d" % index
        payload = _recorded_capture_payload(captured)
        target = CaptureContextNode(
            key=target_key, label=node_label(payload), payload=payload,
            is_target=True, is_semantic=True,
        )
        window.children.append(target)
        captured_by_key[target_key] = captured
        target_keys.append(target_key)

    return CaptureContext(
        framework="mixed" if len({str(item.framework or "") for item in distinct}) > 1
        else str(distinct[0].framework or "desktop"),
        root=root,
        target_key=target_keys[0],
        target_keys=tuple(target_keys),
        captured_by_key=captured_by_key,
    )


def _recorded_capture_identity(captured):
    properties = dict(getattr(captured, "backend_properties", {}) or {})
    normalize = lambda value: str(value or "").strip().casefold()
    scope = (
        normalize(getattr(captured, "application", None)),
        normalize(getattr(captured, "window", None)),
    )
    role = normalize(getattr(captured, "role", None))
    accessible_id = normalize(getattr(captured, "accessible_id", None))
    node_ref = normalize(properties.get("node_ref") or properties.get("ref"))
    if node_ref:
        return ("ref", normalize(getattr(captured, "framework", None)), node_ref)
    if accessible_id:
        return ("accessible-id", *scope, role, accessible_id)

    ordinal = None
    strategy = getattr(captured, "authored_strategy", None)
    if strategy is not None:
        identification = dict(getattr(strategy, "options", {}) or {}).get("identification", {})
        if isinstance(identification, Mapping):
            ordinal = identification.get("ordinal")
    durable = (
        "semantic", *scope, role, normalize(getattr(captured, "name", None)),
        normalize(getattr(captured, "parent_accessible_id", None)),
        normalize(getattr(captured, "parent_role", None)),
        normalize(getattr(captured, "parent_name", None)), ordinal,
    )
    if any(durable[3:]):
        return durable
    return ("geometry", *scope, tuple(getattr(captured, "bounds", ()) or ()))


def _recorded_capture_payload(captured):
    properties = dict(getattr(captured, "backend_properties", {}) or {})
    return {
        "ref": properties.get("node_ref") or properties.get("ref"),
        "window": getattr(captured, "window", None),
        "class": getattr(captured, "native_class", None),
        "id": getattr(captured, "accessible_id", None),
        "accessible_role": getattr(captured, "role", None),
        "accessible_text": getattr(captured, "name", None),
        "bounds": getattr(captured, "bounds", None),
        "properties": properties,
    }


def _build_javafx_context(captured, driver, max_depth=64):
    properties = dict(getattr(captured, "backend_properties", {}) or {})
    target_ref = properties.get("node_ref")
    bridge_pid = properties.get("bridge_pid")
    if not target_ref:
        raise LookupError("captured JavaFX object has no live node reference")

    endpoint = None
    for candidate in driver.endpoints():
        if bridge_pid is None or candidate.pid == bridge_pid:
            endpoint = candidate
            if bridge_pid is not None:
                break
    if endpoint is None:
        raise LookupError("captured JavaFX bridge endpoint is no longer available")

    response = endpoint.request("tree", timeout=4.0, max_depth=max_depth)
    for index, window in enumerate(response.get("windows", [])):
        if not isinstance(window, Mapping) or not isinstance(window.get("root"), Mapping):
            continue
        root_raw = dict(window["root"])
        if not _contains_ref(root_raw, target_ref):
            continue
        target_ref = _resolved_semantic_ref(root_raw, target_ref)
        title = str(window.get("title") or root_raw.get("window") or "JavaFX Window")
        children = _semantic_children(root_raw, target_ref)
        root_payload = dict(root_raw)
        root_payload["window"] = title
        root = CaptureContextNode(
            key=str(root_payload.get("ref") or "window-%s-%s" % (endpoint.pid, index)),
            label=title,
            payload=root_payload,
            children=children,
            is_target=str(root_payload.get("ref")) == str(target_ref),
            is_window_root=True,
            is_semantic=False,
        )
        root.children = _dedupe_root_child(root, root.children)
        context = CaptureContext("javafx", root, str(target_ref), endpoint=endpoint)
        if context.find(str(target_ref)) is None:
            target = _find_raw(root_raw, target_ref)
            if target is not None:
                root.children.append(_context_node(dict(target), target_ref))
        return context
    raise LookupError("captured JavaFX node is no longer present in its window tree")


def _build_fallback_context(captured):
    hierarchy = list(getattr(captured, "hierarchy", ()) or ())
    window = getattr(captured, "window", None) or getattr(captured, "application", None) or "Application Window"
    target_key = "captured-target"
    root = CaptureContextNode(
        key="window-root",
        label=str(window),
        payload={"window": window, "bounds": getattr(captured, "bounds", None)},
        is_window_root=True,
        is_semantic=False,
    )
    current = root
    for index, label in enumerate(hierarchy[:-1]):
        child = CaptureContextNode(
            key="ancestor-%s" % index,
            label=str(label),
            payload={"hierarchy_label": str(label), "window": window},
            is_semantic=False,
        )
        current.children.append(child)
        current = child
    target_payload = {
        "window": window,
        "class": getattr(captured, "native_class", None),
        "id": getattr(captured, "accessible_id", None),
        "accessible_role": getattr(captured, "role", None),
        "accessible_text": getattr(captured, "name", None),
        "bounds": getattr(captured, "bounds", None),
    }
    current.children.append(CaptureContextNode(
        key=target_key,
        label=str(getattr(captured, "name", None) or (hierarchy[-1] if hierarchy else "Captured Object")),
        payload=target_payload,
        is_target=True,
        is_semantic=True,
    ))
    return CaptureContext(str(getattr(captured, "framework", "") or "desktop"), root, target_key)


def _semantic_children(raw, target_ref):
    result = []
    for child in raw.get("children", []) if isinstance(raw.get("children"), list) else []:
        if not isinstance(child, Mapping):
            continue
        result.extend(_semanticize(dict(child), target_ref))
    return result


def _semanticize(raw, target_ref):
    promoted = []
    for child in raw.get("children", []) if isinstance(raw.get("children"), list) else []:
        if isinstance(child, Mapping):
            promoted.extend(_semanticize(dict(child), target_ref))
    is_target = str(raw.get("ref")) == str(target_ref)
    semantic = is_target or is_semantic_node(raw)
    structural = bool(promoted) and is_structural_context_node(raw)
    if not semantic and not structural:
        return promoted
    node = _context_node(raw, target_ref)
    node.is_semantic = semantic
    node.children = promoted
    return [node]


def _context_node(raw, target_ref):
    key = str(raw.get("ref") or "node-%s" % id(raw))
    return CaptureContextNode(
        key=key,
        label=node_label(raw),
        payload=raw,
        is_target=str(raw.get("ref")) == str(target_ref),
    )


def _dedupe_root_child(root, children):
    output = []
    for child in children:
        if child.key == root.key:
            output.extend(child.children)
        else:
            output.append(child)
    return output


def is_semantic_node(node):
    """Mirror the live resolver's interaction-boundary policy for snapshots."""
    class_name = str(node.get("class") or "")
    simple_name = str(node.get("simple_class") or class_name.rsplit(".", 1)[-1])
    role = str(node.get("accessible_role") or "").replace("_", " ").casefold()
    if node.get("semantic_boundary") is True:
        return True
    if node.get("actions"):
        return True
    properties = node.get("properties")
    if isinstance(properties, Mapping):
        if properties.get("automation.semanticBoundary") is True:
            return True
        if properties.get("automation.actions") not in (None, "", [], ()):
            return True
    boundaries = {
        "Button", "ToggleButton", "CheckBox", "RadioButton", "Hyperlink",
        "TextField", "PasswordField", "TextArea", "ComboBox", "ChoiceBox",
        "Spinner", "DatePicker", "Slider", "ListCell", "TableCell",
        "TreeCell", "MenuBar", "MenuButton", "Menu", "MenuItem", "MenuItemContainer", "Tab",
    }
    if simple_name in boundaries:
        return True
    if simple_name.endswith("Control") and _is_application_class(class_name):
        return True
    semantic_roles = {
        "button", "check box", "radio button", "toggle button", "text field",
        "password field", "text area", "combo box", "spinner", "slider",
        "list item", "table cell", "tree item", "menu bar", "menu",
        "menu item", "tab", "hyperlink",
    }
    return role in semantic_roles


def is_structural_context_node(node):
    """Retain stable ancestry for identity without making it saveable."""
    if node.get("id") not in (None, ""):
        return True
    properties = node.get("properties")
    if isinstance(properties, Mapping):
        return any(
            str(key).casefold().startswith(("automation.", "test.", "qa."))
            for key in properties
        )
    return False


def identity_descriptors(node):
    result = {}
    for key in ("id", "class", "accessible_role", "accessible_text", "text", "user_data"):
        value = node.get(key)
        if value not in (None, ""):
            result[key] = value
    layout = node.get("layout")
    if isinstance(layout, Mapping):
        for key, value in layout.items():
            if value is not None:
                result["layout.%s" % key] = value
    properties = node.get("properties")
    if isinstance(properties, Mapping):
        for key, value in properties.items():
            folded = str(key).casefold()
            if folded.startswith(("automation.", "test.", "qa.")) and value is not None:
                result["properties.%s" % key] = value
    return result


def stable_descriptor(node):
    result = {}
    for key in ("id", "accessible_text", "text"):
        value = node.get(key)
        if value not in (None, ""):
            result[key] = value
            break
    class_name = node.get("class")
    if class_name and not _is_internal_class(str(class_name)):
        result["class"] = class_name
    role = node.get("accessible_role")
    if role and role not in {"PARENT", "NODE"}:
        result["accessible_role"] = role
    return result


def node_label(node):
    for key in ("id", "accessible_text", "text"):
        value = node.get(key)
        if value not in (None, ""):
            return str(value)
    simple = node.get("simple_class")
    if simple:
        label = str(simple)
    else:
        label = str(node.get("class") or "Node").rsplit(".", 1)[-1]
    layout = node.get("layout")
    if isinstance(layout, Mapping) and ("grid_row" in layout or "grid_column" in layout):
        label += " [%s,%s]" % (layout.get("grid_row", 0), layout.get("grid_column", 0))
    return label


def suggested_name(node):
    for key in ("id", "accessible_text", "text", "simple_class"):
        value = node.get(key)
        if value not in (None, ""):
            return _identifier(str(value))
    return "object"


def _identifier(value):
    output = []
    previous_separator = False
    for char in value.strip():
        if char.isalnum() or char in {"_", "-", "."}:
            output.append(char)
            previous_separator = False
        elif not previous_separator:
            output.append("_")
            previous_separator = True
    result = "".join(output).strip("_")
    return result or "object"


def _contains_ref(node, target_ref):
    if str(node.get("ref")) == str(target_ref):
        return True
    for child in node.get("children", []) if isinstance(node.get("children"), list) else []:
        if isinstance(child, Mapping) and _contains_ref(child, target_ref):
            return True
    return False


def _find_raw(node, target_ref):
    if str(node.get("ref")) == str(target_ref):
        return node
    for child in node.get("children", []) if isinstance(node.get("children"), list) else []:
        if isinstance(child, Mapping):
            found = _find_raw(child, target_ref)
            if found is not None:
                return found
    return None


def _resolved_semantic_ref(root, target_ref):
    """Promote a serialized physical target to its nearest semantic ancestor."""
    path = []

    def visit(node):
        path.append(node)
        if str(node.get("ref")) == str(target_ref):
            return True
        for child in node.get("children", []) if isinstance(node.get("children"), list) else []:
            if isinstance(child, Mapping) and visit(child):
                return True
        path.pop()
        return False

    if not visit(root):
        return target_ref
    for node in reversed(path):
        if is_semantic_node(node):
            return str(node.get("ref"))
    return target_ref


def _is_application_class(class_name):
    return bool(class_name and not class_name.startswith(("java.", "javax.", "javafx.", "com.sun.")))


def _is_internal_class(class_name):
    return class_name.startswith(("com.sun.javafx.", "com.sun.glass."))
