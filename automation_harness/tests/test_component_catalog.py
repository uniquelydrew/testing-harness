from automation_harness.core.component_catalog import (
    canonical_action_types,
    component_catalog,
    component_spec,
    display_name,
    is_fallback_type,
)
from automation_harness.models.gui import ActionType, ObjectType


def test_component_catalog_loads_versioned_user_facing_vocabulary():
    catalog = component_catalog()
    assert catalog["version"] == 1
    assert "button" in catalog["types"]
    assert "hbox" not in catalog["types"]
    assert "jpanel" not in catalog["types"]


def test_menu_public_actions_are_deliberately_small():
    assert canonical_action_types(ObjectType.MENU) == frozenset({
        ActionType.CLICK,
        ActionType.SELECT_MENU_ITEM,
    })


def test_panel_is_explicit_fallback_not_framework_variant():
    assert display_name(ObjectType.PANEL) == "Panel"
    assert component_spec(ObjectType.PANEL)["container"] is True
    assert is_fallback_type(ObjectType.PANEL) is True



def test_every_object_type_has_explicit_canonical_contract():
    catalog = component_catalog()["types"]
    missing = sorted(
        object_type.value
        for object_type in ObjectType
        if object_type.value not in catalog
    )
    assert missing == []


def test_catalog_never_advertises_semantic_actions_without_public_definition():
    public = set()
    from automation_harness.authoring.action_catalog import INTERACTIONS
    public.update(action.value for action in INTERACTIONS)
    for key, spec in component_catalog()["types"].items():
        unknown = set(spec.get("actions", ())) - public
        assert unknown == set(), "%s exposes unsupported actions %r" % (key, unknown)
