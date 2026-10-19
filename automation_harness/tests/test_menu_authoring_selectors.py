"""Contracts for authored menu routes and generalized option selectors."""
from types import SimpleNamespace

import pytest

from automation_harness.core.component_handle import ComponentHandle
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.menu_navigation import selectors_from_navigation
from automation_harness.core.test_plan import validate_plan_components
from automation_harness.models.component import ComponentDefinition
from automation_harness.models.gui import GuiAction, ObjectType
from automation_harness.models.plan import StepCall, TestPlan


MENU = {
    "file": {
        "kind": "menu",
        "display_name": "File",
        "criteria": {"id": "fileMenu", "text": "File"},
        "subobjects": {
            "open": {"kind": "menu_item", "criteria": {"id": "openMenuItem", "text": "Open"}},
            "disabled": {
                "kind": "menu_item", "criteria": {"text": "Disabled"},
                "enabled": False,
            },
            "separator": {"kind": "separator", "criteria": {"text": "---"}},
            "header": {
                "kind": "menu_item", "criteria": {"text": "Header"},
                "selectable": False,
            },
        },
    },
}


def _menu_plan(route):
    definition = ComponentDefinition(
        "main.menu", object_type=ObjectType.MENU_BAR,
        actions=frozenset({"resolve", "select_menu_item"}),
        subobjects=MENU,
    )
    repository = ComponentRepository({"main.menu": definition})
    plan = TestPlan(name="menu", steps=(
        StepCall("menu-001", "gui.object.action", inputs={
            "component_id": definition.component_id,
            "action": {"type": "select_menu_item", "path": route},
        }),
    ))
    return plan, repository


def test_known_menu_route_reuses_durable_criteria():
    selectors = selectors_from_navigation(MENU, "File > Open")
    assert selectors == [
        {"kind": "menu", "criteria": {"id": "fileMenu", "text": "File"}},
        {"kind": "menu_item", "criteria": {"id": "openMenuItem", "text": "Open"}},
    ]


def test_unknown_menu_leaf_uses_text_fallback_without_losing_known_ancestors():
    assert selectors_from_navigation(MENU, "File > Save As") == [
        {"kind": "menu", "criteria": {"id": "fileMenu", "text": "File"}},
        {"kind": "menu_item", "criteria": {"text": "Save As"}},
    ]


def test_absent_inventory_accepts_explicit_nested_menu_route():
    assert selectors_from_navigation({}, "File > Export > PDF") == [
        {"kind": "menu", "criteria": {"text": "File"}},
        {"kind": "menu", "criteria": {"text": "Export"}},
        {"kind": "menu_item", "criteria": {"text": "PDF"}},
    ]


@pytest.mark.parametrize("route", ["File > Disabled", "File > Header", "File > ---"])
def test_known_inactive_or_unselectable_options_are_rejected(route):
    with pytest.raises(ValueError):
        selectors_from_navigation(MENU, route)


def test_disabled_parent_rejects_nested_option():
    submenu = {
        "file": {
            "kind": "menu", "criteria": {"text": "File"}, "enabled": False,
            "subobjects": {"open": {"kind": "menu_item", "criteria": {"text": "Open"}}},
        },
    }
    with pytest.raises(ValueError, match="disabled"):
        selectors_from_navigation(submenu, "File > Open")


def test_ambiguous_menu_label_is_not_guessed():
    options = {
        "first": {"kind": "menu_item", "criteria": {"text": "Open"}},
        "second": {"kind": "menu_item", "criteria": {"text": "Open"}},
    }
    with pytest.raises(ValueError, match="ambiguous"):
        selectors_from_navigation(options, "Open")


@pytest.mark.parametrize("route", ["", "File > ", " > Open"])
def test_empty_or_incomplete_manual_routes_are_rejected(route):
    with pytest.raises(ValueError, match="menu navigation"):
        selectors_from_navigation(MENU, route)


def test_legacy_internal_id_paths_remain_strict():
    assert selectors_from_navigation(MENU, ["file", "open"])[1]["criteria"]["id"] == "openMenuItem"
    with pytest.raises(ValueError):
        selectors_from_navigation(MENU, ["file", "not-captured"])


def test_preflight_accepts_authored_uncaptured_menu_leaf():
    plan, repository = _menu_plan("File > Save As")
    assert validate_plan_components(plan, repository) == []


def test_preflight_rejects_known_disabled_menu_leaf():
    plan, repository = _menu_plan("File > Disabled")
    assert any("disabled" in issue for issue in validate_plan_components(plan, repository))


def test_preflight_rejects_unresolved_legacy_id_path():
    plan, repository = _menu_plan(["file", "absent"])
    assert any("invalid menu navigation" in issue for issue in validate_plan_components(plan, repository))


@pytest.mark.parametrize("input_selector,expected_value,expected_criteria", [
    ("North", "North", None),
    (2, 2, None),
    ({"name": "North"}, None, {"name": "North"}),
    ({"index": 2}, None, {"index": 2}),
])
def test_authored_selector_shorthand_normalizes(input_selector, expected_value, expected_criteria):
    action = GuiAction.from_value({"type": "select_item", "selector": input_selector})
    assert action.value == expected_value
    if expected_criteria is None:
        assert action.selector is None
    else:
        assert dict(action.selector.criteria) == expected_criteria


def test_nonselection_actions_keep_structured_selector_contract():
    with pytest.raises(ValueError, match="selector"):
        GuiAction.from_value({"type": "click", "selector": "North"})


def test_menu_execution_uses_same_scoped_selectors_as_preflight(monkeypatch):
    _plan, repository = _menu_plan("File > Save As")
    handle = ComponentHandle(
        SimpleNamespace(evidence=SimpleNamespace(record=lambda *_args, **_kw: None)),
        repository.get("main.menu"),
    )
    recorded = []
    monkeypatch.setattr(ComponentHandle, "prepare_for_interaction",
                        lambda *_args, **_kw: SimpleNamespace(strategy="none", to_dict=lambda: {}))
    monkeypatch.setattr(ComponentHandle, "_accessibility_operation",
                        lambda _self, _label, method, selectors: recorded.append((method, selectors)) or {})
    result = handle.execute({"type": "select_menu_item", "path": "File > Save As"})
    assert result.action.value == "select_menu_item"
    assert recorded == [("select_menu_path", selectors_from_navigation(MENU, "File > Save As"))]


@pytest.mark.parametrize("selector,expected", [
    ("North", {"text": "North"}),
    ({"name": "North"}, {"text": "North"}),
    ({"kind": "option", "criteria": {"text": "North"}}, {"text": "North"}),
])
def test_combo_selection_executes_authored_selector(monkeypatch, selector, expected):
    definition = ComponentDefinition(
        "choice", object_type=ObjectType.COMBO_BOX,
        actions=frozenset({"select_item"}),
    )
    handle = ComponentHandle(
        SimpleNamespace(evidence=SimpleNamespace(record=lambda *_args, **_kw: None)),
        definition,
    )
    recorded = []
    monkeypatch.setattr(ComponentHandle, "prepare_for_interaction",
                        lambda *_args, **_kw: SimpleNamespace(strategy="none", to_dict=lambda: {}))
    monkeypatch.setattr(ComponentHandle, "_accessibility_operation",
                        lambda _self, _label, method, value: recorded.append((method, value)) or {})
    handle.execute({"type": "select_item", "selector": selector})
    assert recorded == [("select_popup_path", [{"kind": "popup_item", "criteria": expected}])]
