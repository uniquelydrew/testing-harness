"""Regression coverage for explicit plan/OR object ownership."""
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.object_reconciliation import object_conflicts


class Definition:
    def __init__(self, object_id):
        self.object_id = object_id


def test_same_name_and_identity_requires_reconciliation():
    repository = ComponentRepository.__new__(ComponentRepository)
    object.__setattr__(repository, "components", {"File Menu": Definition("id-1")})
    issues = object_conflicts({"File Menu": {"object_id": "id-1"}}, repository)
    assert len(issues) == 1
    assert "defined in both" in issues[0]


def test_renamed_identity_requires_reconciliation():
    repository = ComponentRepository.__new__(ComponentRepository)
    object.__setattr__(repository, "components", {"Window.File Menu": Definition("id-1")})
    issues = object_conflicts({"File Menu": {"object_id": "id-1"}}, repository)
    assert len(issues) == 1
    assert "share immutable object_id" in issues[0]


def test_name_collision_requires_reconciliation():
    repository = ComponentRepository.__new__(ComponentRepository)
    object.__setattr__(repository, "components", {"File Menu": Definition("id-1")})
    issues = object_conflicts({"File Menu": {"object_id": "id-2"}}, repository)
    assert len(issues) == 1
    assert "different identities" in issues[0]


def test_disjoint_plan_local_object_is_allowed():
    repository = ComponentRepository.__new__(ComponentRepository)
    object.__setattr__(repository, "components", {"File Menu": Definition("id-1")})
    assert object_conflicts({"Choice Box": {"object_id": "id-2"}}, repository) == []
