from pathlib import Path

import pytest

from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.repository_scope import (
    OVERRIDE_PROPERTY, RepositoryAssociation, RepositoryCompositionError,
    RepositoryScope, RepositorySet,
)
from automation_harness.models.component import ComponentDefinition, ComponentStrategy


def _object(name, object_id, **properties):
    return ComponentDefinition(
        component_id=name, object_id=object_id,
        strategies=(ComponentStrategy("javafx", {"identification": {"mandatory": {"name": name}}}),),
        properties=properties,
    )


def _association(name, scope):
    return RepositoryAssociation(Path(name), scope)


def test_composition_keeps_distinct_shared_objects():
    first = _object("FileMenu", "10000000-0000-0000-0000-000000000001")
    second = _object("EditMenu", "10000000-0000-0000-0000-000000000002")
    result = RepositorySet(
        (_association("one", RepositoryScope.SHARED), _association("two", RepositoryScope.SHARED)),
        (ComponentRepository({first.component_id: first}), ComponentRepository({second.component_id: second})),
    ).compose()
    assert result.object_id_for("FileMenu") == first.object_id
    assert result.object_id_for("EditMenu") == second.object_id


def test_same_display_name_is_not_silently_merged():
    first = _object("Menu", "10000000-0000-0000-0000-000000000001")
    second = _object("Menu", "10000000-0000-0000-0000-000000000002")
    repository_set = RepositorySet(
        (_association("one", RepositoryScope.SHARED), _association("two", RepositoryScope.SHARED)),
        (ComponentRepository({"Menu": first}), ComponentRepository({"Menu": second})),
    )
    with pytest.raises(RepositoryCompositionError, match="display name"):
        repository_set.compose()


def test_local_override_requires_and_preserves_shared_uuid():
    object_id = "10000000-0000-0000-0000-000000000001"
    shared = _object("FileMenu", object_id)
    local = _object("FileMenuForTest", "20000000-0000-0000-0000-000000000002", **{OVERRIDE_PROPERTY: object_id})
    result = RepositorySet(
        (_association("local", RepositoryScope.LOCAL), _association("shared", RepositoryScope.SHARED)),
        (ComponentRepository({local.component_id: local}), ComponentRepository({shared.component_id: shared})),
    ).compose()
    assert list(result.components) == ["FileMenuForTest"]
    assert result.get(object_id).component_id == "FileMenuForTest"


def test_local_override_of_unknown_uuid_fails():
    local = _object("Menu", "20000000-0000-0000-0000-000000000002", **{
        OVERRIDE_PROPERTY: "10000000-0000-0000-0000-000000000001",
    })
    repository_set = RepositorySet(
        (_association("local", RepositoryScope.LOCAL),),
        (ComponentRepository({local.component_id: local}),),
    )
    with pytest.raises(RepositoryCompositionError, match="unknown object id"):
        repository_set.compose()
