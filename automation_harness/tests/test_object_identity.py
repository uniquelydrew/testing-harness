from __future__ import annotations

from dataclasses import replace

from automation_harness.core.component_handle import ComponentHandle
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.models.component import ComponentDefinition, ComponentStrategy
from automation_harness.models.gui import ObjectType


def _component(name: str) -> ComponentDefinition:
    return ComponentDefinition(
        component_id=name,
        description="identity test",
        object_type=ObjectType.BUTTON,
        strategies=(
            ComponentStrategy(
                "atspi",
                {"identification": {"mandatory": {"name": name, "role": "push button"}}},
            ),
        ),
        actions=frozenset({"click"}),
    )


def test_repository_resolves_by_name_and_immutable_object_id():
    definition = _component("dialog.ok")
    repository = ComponentRepository({definition.component_id: definition})

    assert repository.get("dialog.ok") is definition
    assert repository.get(definition.object_id) is definition
    assert repository.object_id_for("dialog.ok") == definition.object_id


def test_runtime_gui_identity_uses_immutable_id_and_retains_display_name():
    definition = _component("dialog.ok")
    handle = ComponentHandle(object(), definition)

    assert handle.identity.repository_id == definition.object_id
    assert handle.identity.name == "dialog.ok"


def test_rename_preserves_immutable_object_id():
    definition = _component("dialog.ok")
    repository = ComponentRepository({definition.component_id: definition})

    renamed = repository.rename("dialog.ok", "dialog.confirm")

    assert renamed.get("dialog.confirm").object_id == definition.object_id
    assert renamed.get(definition.object_id).component_id == "dialog.confirm"


def test_recapture_preserves_existing_identity():
    original = _component("dialog.ok")
    repository = ComponentRepository({original.component_id: original})
    recaptured = replace(original, description="recaptured", object_id=_component("other").object_id)

    updated = repository.with_component(recaptured)

    assert updated.get("dialog.ok").object_id == original.object_id
    assert updated.get("dialog.ok").description == "recaptured"


def test_legacy_repository_identity_is_deterministic():
    document = {
        "version": 2,
        "components": {
            "dialog.ok": {
                "object_type": "button",
                "actions": ["click"],
                "strategies": [
                    {
                        "type": "atspi",
                        "identification": {"mandatory": {"name": "OK", "role": "push button"}},
                    }
                ],
            }
        },
    }

    first = ComponentRepository.from_document(document)
    second = ComponentRepository.from_document(document)

    assert first.get("dialog.ok").object_id == second.get("dialog.ok").object_id
    assert first.to_document()["version"] == 3
