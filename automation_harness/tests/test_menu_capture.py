from dataclasses import dataclass, field

from automation_harness.drivers.atspi_driver import _menu_subobjects


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


def test_menu_capture_queries_nested_submenus():
    recent = _Accessible("Recent", "menu", [_Accessible("Report.yaml", "menu item")])
    root = _Accessible("File", "menu", [_Accessible("Open", "menu item"), recent])
    subobjects = _menu_subobjects(root)
    assert set(subobjects) == {"open", "recent"}
    assert set(subobjects["recent"]["subobjects"]) == {"report_yaml"}
