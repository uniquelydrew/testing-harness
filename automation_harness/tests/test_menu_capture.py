from dataclasses import dataclass, field

from automation_harness.drivers.atspi_driver import AtspiDriver, _menu_subobjects


@dataclass
class _Accessible:
    name: str
    role: str
    children: list = field(default_factory=list)

    @property
    def childCount(self):
        return len(self.children)

    def getChildAtIndex(self, index):
        return self.children[index]

    def getRoleName(self):
        return self.role

    def getAttributes(self):
        return []


class _Actions:
    nActions = 1

    def __init__(self, owner):
        self.owner = owner

    def getName(self, _index):
        return "click"

    def doAction(self, _index):
        self.owner.activated = True
        return True


class _ActionAccessible(_Accessible):
    activated: bool = False

    def queryAction(self):
        return _Actions(self)


def test_menu_capture_queries_nested_submenus():
    recent = _Accessible("Recent", "menu", [_Accessible("Report.yaml", "menu item")])
    root = _Accessible("File", "menu", [_Accessible("Open", "menu item"), recent])
    subobjects = _menu_subobjects(root)
    assert set(subobjects) == {"open", "recent"}
    assert set(subobjects["recent"]["subobjects"]) == {"report_yaml"}


def test_menu_path_is_traversed_and_terminal_item_activated(monkeypatch):
    report = _ActionAccessible("Report.yaml", "menu item")
    recent = _ActionAccessible("Recent", "menu", [report])
    file_menu = _ActionAccessible("File", "menu", [recent])
    root = _Accessible("Application", "menu bar", [file_menu])
    monkeypatch.setattr(
        "automation_harness.drivers.atspi_driver._select_accessible",
        lambda _desktop, _identity: (root, ()),
    )
    monkeypatch.setattr(
        "automation_harness.drivers.atspi_driver._pyatspi",
        lambda: type("Api", (), {"Registry": type("Registry", (), {"getDesktop": staticmethod(lambda _index: object())})}),
    )
    result = AtspiDriver().select_menu_path(
        [
            {"kind": "menu", "criteria": {"name": "File", "role": "menu"}, "ordinal": 0},
            {"kind": "menu", "criteria": {"name": "Recent", "role": "menu"}, "ordinal": 0},
            {"kind": "menu_item", "criteria": {"name": "Report.yaml", "role": "menu item"}, "ordinal": 0},
        ],
        identification={"mandatory": {"name": "Application"}},
    )
    assert report.activated is True
    assert result["action"] == "select_menu_item"
    assert [item["name"] for item in result["path"]] == ["File", "Recent", "Report.yaml"]
