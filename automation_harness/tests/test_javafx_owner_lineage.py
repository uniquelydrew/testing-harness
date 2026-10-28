"""Preserve canonical window ownership through JavaFX object lineage."""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from automation_harness.core.component_handle import ComponentHandle
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.object_ownership import ObjectOwnershipError, owning_window_hint
from automation_harness.models.component import ComponentDefinition, ComponentStrategy
from automation_harness.models.gui import ObjectType, ActionType


WINDOW = "Select Site"
MAIN = "ERSA Main Video Display"


def make_owner(window=WINDOW, *, scope=None):
    return ComponentDefinition(
        "Dialog Owner", object_id=str(uuid4()), object_type=ObjectType.WINDOW,
        strategies=(ComponentStrategy("javafx", {
            "identification": {"mandatory": {"id": "dialogRoot"},
                               "assistive": {"window": window}},
        }),), scope=scope or {},
    )


@pytest.mark.parametrize("type_", [
    ObjectType.BUTTON, ObjectType.TOGGLE_BUTTON, ObjectType.TEXT_FIELD,
    ObjectType.COMBO_BOX, ObjectType.PANEL, ObjectType.CUSTOM, ObjectType.MENU,
])
def test_child_inherits_owner_window_without_overwriting_canonical_identity(type_):
    owner = make_owner()
    middle = ComponentDefinition(
        "Container", object_id=str(uuid4()), object_type=ObjectType.PANEL,
        owner_object_id=owner.object_id,
    )
    child = ComponentDefinition(
        "Control", object_id=str(uuid4()), object_type=type_,
        owner_object_id=middle.object_id,
        strategies=(ComponentStrategy("javafx", {
            "identification": {"mandatory": {"id": "target"}},
        }),),
    )
    repository = ComponentRepository({
        owner.component_id: owner, middle.component_id: middle, child.component_id: child,
    })
    ctx = SimpleNamespace(components=repository)
    handle = ComponentHandle(ctx, child)
    assert handle._owning_javafx_window() == WINDOW
    assert handle._scoped_identification(
        "javafx", child.strategies[0].options["identification"]
    ) == {"mandatory": {"id": "target"}, "assistive": {"window": WINDOW}}
    assert child.scope == {}
    assert "assistive" not in child.strategies[0].options["identification"]


def test_nearest_child_scope_takes_priority_over_ancestor_window():
    owner = make_owner()
    child = ComponentDefinition(
        "Popup Target", owner_object_id=owner.object_id,
        scope={"ownership": {"window": "Popup Window"}},
    )
    repo = ComponentRepository({owner.component_id: owner, child.component_id: child})
    assert owning_window_hint(child, repo) == "Popup Window"


def test_repository_rejects_missing_ancestor_before_execution():
    from automation_harness.core.component_repository import ComponentRepositoryError

    child = ComponentDefinition("Lost", owner_object_id=str(uuid4()))
    with pytest.raises(ComponentRepositoryError, match="does not identify"):
        ComponentRepository({child.component_id: child})


def test_repository_rejects_ownership_cycles_before_execution():
    from automation_harness.core.component_repository import ComponentRepositoryError

    aid, bid = str(uuid4()), str(uuid4())
    a = ComponentDefinition("A", object_id=aid, owner_object_id=bid)
    b = ComponentDefinition("B", object_id=bid, owner_object_id=aid)
    with pytest.raises(ComponentRepositoryError, match="cyclic ownership"):
        ComponentRepository({"A": a, "B": b})


def test_owner_scope_can_supply_window_even_without_owner_locator():
    owner = make_owner(scope={"ownership": {"window": WINDOW}})
    child = ComponentDefinition("Child", owner_object_id=owner.object_id)
    repo = ComponentRepository({owner.component_id: owner, child.component_id: child})
    assert owning_window_hint(child, repo) == WINDOW


def test_missing_direct_scope_but_parent_window_produces_preparation_hint(monkeypatch):
    owner = make_owner()
    child = ComponentDefinition(
        "Child", object_id=str(uuid4()), owner_object_id=owner.object_id,
        object_type=ObjectType.BUTTON,
        strategies=(ComponentStrategy("javafx", {"identification": {
            "mandatory": {"id": "target"}
        }}),),
    )
    repo = ComponentRepository({owner.component_id: owner, child.component_id: child})
    observed = []
    class Driver:
        def activate_window(self, **kwargs):
            observed.append(kwargs)
            return {"window": WINDOW, "focused": True}
    monkeypatch.setattr(
        "automation_harness.core.component_handle.JavaFxBridgeDriver",
        lambda context: Driver(),
    )
    events = SimpleNamespace(record=lambda *_args, **_kwargs: None)
    handle = ComponentHandle(SimpleNamespace(
        components=repo, backend="live-desktop", evidence=events,
    ), child)
    outcome = handle.prepare_for_interaction(ActionType.CLICK)
    assert outcome.successful
    assert observed[0]["window"] == WINDOW
    assert observed[0]["identification"]["assistive"]["window"] == WINDOW
