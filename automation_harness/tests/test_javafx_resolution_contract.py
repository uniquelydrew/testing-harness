"""Regression contract for native JavaFX selection and canonical ownership.

Source-level checks complement bridge integration tests. The native JavaFX
resolver runs in the AUT JVM, where test CI cannot create an ERSA scene graph.
"""
from __future__ import annotations

from pathlib import Path

from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.component_handle import ComponentHandle
from automation_harness.models.component import ComponentDefinition, ComponentStrategy
from automation_harness.models.gui import ObjectType
from types import SimpleNamespace


JAVA = (
    Path(__file__).resolve().parents[2]
    / "javafx_agent/src/main/java/com/automationharness/javafx/AutomationHarnessJavaFxAgent.java"
)


def _native_resolver():
    source = JAVA.read_text(encoding="utf-8")
    start = source.index("private static Resolution resolve(")
    end = source.index("private static List<NodeMatch> filter(", start)
    return source[start:end]


def test_window_is_a_hard_requirement_even_when_target_id_is_unique():
    source = _native_resolver()
    assert 'if (assistive.containsKey("window"))' in source
    assert 'matches = filter(matches, criterion);' in source
    assert source.index('if (assistive.containsKey("window"))') < source.index(
        'if (matches.size() > 1 && assistive.containsKey("class"))'
    )
    # The old loop stopped at a unique ID before checking window ownership.
    assert source.index('if (assistive.containsKey("window"))') < source.index(
        'if (matches.size() <= 1)'
    )


def test_native_class_precedes_volatile_assistive_text_and_menu_names():
    source = _native_resolver()
    assert source.index('if (assistive.containsKey("window"))') < source.index(
        'assistive.containsKey("class")'
    ) < source.index('for (Map.Entry<String, Object> entry : assistive.entrySet())')
    assert '"class".equals(entry.getKey())' in source


def test_duplicate_logical_menu_objects_are_counted_once():
    source = JAVA.read_text(encoding="utf-8")
    start = source.index("private static List<NodeMatch> allNodeMatches()")
    end = source.index("private static List<Object> flatten(", start)
    body = source[start:end]
    assert 'if (!boolCall(window, "isShowing", false))' in body
    assert "new IdentityHashMap<Object, Boolean>()" in body
    assert "seen.put(child, Boolean.TRUE)" in body
    assert "seen.put(node, Boolean.TRUE)" in body


def test_canonical_identity_is_not_replaced_to_work_around_duplicate_ids():
    menu = ComponentDefinition(
        "File Menu", object_type=ObjectType.MENU, framework="javafx",
        strategies=(ComponentStrategy("javafx", {"identification": {
            "mandatory": {"id": "fileMenu"},
            "assistive": {
                "accessible_text": "File Menu",
                "class": "javafx.scene.control.Menu",
            },
        }}),),
        scope={"ownership": {"window": "ERSA Main Video Display"}},
    )
    repo = ComponentRepository({menu.component_id: menu})
    ctx = SimpleNamespace(components=repo)
    handle = ComponentHandle(ctx, repo.get(menu.object_id))
    original = menu.strategies[0].options["identification"]
    scoped = handle._scoped_identification("javafx", original)
    assert scoped["mandatory"] == {"id": "fileMenu"}
    assert scoped["assistive"]["window"] == "ERSA Main Video Display"
    assert menu.strategies[0].options["identification"] == original
    assert menu.object_id == repo.get(menu.object_id).object_id
