from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from automation_harness.drivers.atspi_driver import (
    AtspiAmbiguousObject,
    AtspiObjectNotFound,
    _select_accessible,
)
from automation_harness.models.component import AtspiIdentification


@dataclass
class FakeAccessible:
    name: str | None
    role: str
    accessible_id: str | None = None
    children: list["FakeAccessible"] = field(default_factory=list)
    parent: "FakeAccessible | None" = field(default=None, init=False, repr=False)

    def __post_init__(self):
        for child in self.children:
            child.parent = self

    @property
    def childCount(self) -> int:
        return len(self.children)

    def getChildAtIndex(self, index: int) -> "FakeAccessible":
        return self.children[index]

    def getRoleName(self) -> str:
        return self.role

    def getAttributes(self) -> list[str]:
        return [f"id:{self.accessible_id}"] if self.accessible_id else []


def _tree() -> tuple[FakeAccessible, FakeAccessible, FakeAccessible, FakeAccessible]:
    follow_primary = FakeAccessible("Follow", "push button", "follow-primary")
    follow_secondary = FakeAccessible("Follow", "push button", "follow-secondary")
    toolbar_primary = FakeAccessible("Tracking", "tool bar", children=[follow_primary])
    toolbar_secondary = FakeAccessible("Other", "tool bar", children=[follow_secondary])
    window = FakeAccessible("Tracking Window", "frame", children=[toolbar_primary, toolbar_secondary])
    app = FakeAccessible("Reference Application", "application", children=[window])
    desktop = FakeAccessible("Desktop", "desktop", children=[app])
    return desktop, follow_primary, follow_secondary, toolbar_primary


def test_mandatory_conditions_are_conjunctive():
    desktop, primary, _secondary, _toolbar = _tree()
    match, trace = _select_accessible(
        desktop,
        AtspiIdentification(
            mandatory={
                "name": "Follow",
                "role": "push button",
                "accessible_id": "follow-primary",
            }
        ),
    )
    assert match is primary
    assert len(trace) == 1
    assert trace[0].source == "mandatory"
    assert trace[0].matches == 1


def test_assistive_conditions_progressively_disambiguate():
    desktop, primary, _secondary, _toolbar = _tree()
    match, trace = _select_accessible(
        desktop,
        AtspiIdentification(
            mandatory={"name": "Follow", "role": "push button"},
            assistive={
                "application": "Reference Application",
                "window": "Tracking Window",
                "parent": {"name": "Tracking", "role": "tool bar"},
            },
        ),
    )
    assert match is primary
    assert [stage.source for stage in trace] == [
        "mandatory",
        "assistive:application",
        "assistive:window",
        "assistive:parent",
    ]
    assert [stage.matches for stage in trace] == [2, 2, 2, 1]


def test_ambiguous_complete_locator_never_selects_first_candidate():
    desktop, _primary, _secondary, _toolbar = _tree()
    with pytest.raises(AtspiAmbiguousObject, match="2 runtime objects match"):
        _select_accessible(
            desktop,
            AtspiIdentification(
                mandatory={"name": "Follow", "role": "push button"},
                assistive={"window": "Tracking Window"},
            ),
        )


def test_explicit_ordinal_is_a_last_resort_not_an_implicit_default():
    desktop, _primary, secondary, _toolbar = _tree()
    match, trace = _select_accessible(
        desktop,
        AtspiIdentification(
            mandatory={"name": "Follow", "role": "push button"},
            ordinal=1,
        ),
    )
    assert match is secondary
    assert trace[-1].source == "ordinal:1"


def test_out_of_range_ordinal_is_not_found():
    desktop, _primary, _secondary, _toolbar = _tree()
    with pytest.raises(AtspiObjectNotFound, match="outside 2 matching candidates"):
        _select_accessible(
            desktop,
            AtspiIdentification(
                mandatory={"name": "Follow", "role": "push button"},
                ordinal=2,
            ),
        )
