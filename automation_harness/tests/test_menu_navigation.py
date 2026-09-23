import pytest

from automation_harness.core.menu_navigation import (
    navigation_from_path,
    resolve_navigation,
)


MENU = {
    "file": {
        "kind": "menu",
        "display_name": "File",
        "criteria": {"id": "fileMenu", "text": "File"},
        "subobjects": {
            "export": {
                "kind": "menu",
                "display_name": "Export",
                "criteria": {"text": "Export"},
                "subobjects": {
                    "pdf": {
                        "kind": "menu_item",
                        "display_name": "PDF",
                        "criteria": {"id": "pdfItem", "text": "PDF"},
                    },
                },
            },
        },
    },
}


def test_navigation_string_is_derived_from_user_facing_labels():
    assert navigation_from_path(
        MENU, ["file", "export", "pdf"],
    ) == "File > Export > PDF"


def test_navigation_string_resolves_back_to_internal_subobject_path():
    assert resolve_navigation(
        MENU, "File > Export > PDF",
    ) == ["file", "export", "pdf"]


def test_legacy_list_valued_menu_paths_remain_supported():
    assert resolve_navigation(
        MENU, ["file", "export", "pdf"],
    ) == ["file", "export", "pdf"]


def test_navigation_may_use_stable_internal_id_as_a_segment():
    assert resolve_navigation(
        MENU, "file > export > pdfItem",
    ) == ["file", "export", "pdf"]


def test_ambiguous_navigation_segment_is_rejected():
    duplicated = {
        "one": {"kind": "menu_item", "display_name": "Open", "criteria": {"text": "Open"}},
        "two": {"kind": "menu_item", "display_name": "Open", "criteria": {"text": "Open"}},
    }
    with pytest.raises(ValueError, match="ambiguous"):
        resolve_navigation(duplicated, "Open")
