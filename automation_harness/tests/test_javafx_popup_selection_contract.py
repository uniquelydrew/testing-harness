"""Contracts for JavaFX ChoiceBox/ComboBox native popup selection.

The JavaFX agent is dependency-free at compile time, so these static guards
supplement its javac smoke build. Live JavaFX selection still needs RHEL GUI
qualification with an actual owner and item model.
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BRIDGE = (ROOT / "javafx_agent" / "src" / "main" / "java" / "com"
          / "automationharness" / "javafx" / "AutomationHarnessJavaFxAgent.java")


def _selection_source() -> str:
    source = BRIDGE.read_text(encoding="utf-8")
    return source.split("static Map<String, Object> selectPopupPath(", 1)[1].split(
        "private static Object menuChild(", 1
    )[0]


def test_popup_selection_uses_fx_application_thread_for_resolve_and_model_access():
    source = _selection_source()
    assert "return onFx(() -> {" in source
    assert source.index("return onFx(() -> {") < source.index("unique(resolve(")
    assert source.index("unique(resolve(") < source.index('call(root.node, "getItems")')
    assert source.index('call(root.node, "getItems")') < source.index('getMethod("select", int.class)')


def test_popup_selection_checks_display_converter_and_original_value():
    source = _selection_source()
    assert 'call(root.node, "getConverter")' in source
    assert "wanted.equals(modelText) || wanted.equals(displayText)" in source
    assert '"javafx.util.StringConverter"' in source


def test_popup_selection_fails_closed_on_ambiguity_and_stale_index():
    source = _selection_source()
    assert "matches.size() != 1" in source
    assert "matches.contains(match)" in source
    assert 'call(model, "getSelectedIndex")' in source


def test_popup_missing_item_reports_bounded_inventory():
    source = _selection_source()
    assert "item_count=" in source
    assert "available=" in source
    assert "index < 25" in source
    assert "boundedPopupLabel" in source
