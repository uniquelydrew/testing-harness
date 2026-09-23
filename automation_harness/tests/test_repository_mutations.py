from uuid import uuid4

from automation_harness.authoring.repository_events import publish, subscribe, unsubscribe
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.models.component import ComponentDefinition, ComponentStrategy


def _definition(name, owner=None):
    return ComponentDefinition(component_id=name, object_id=str(uuid4()), owner_object_id=owner)


def test_delete_subtree_removes_owned_descendants_only():
    root = _definition("root")
    child = _definition("child", root.object_id)
    grandchild = _definition("grandchild", child.object_id)
    peer = _definition("peer")
    repository = ComponentRepository({item.component_id: item for item in (root, child, grandchild, peer)})

    updated, removed = repository.delete_subtree(root.object_id)

    assert removed == ("root", "child", "grandchild")
    assert tuple(updated.components) == ("peer",)


def test_repository_change_notifications_are_path_scoped(tmp_path):
    observed = []
    def record(path):
        observed.append(path)
    token = subscribe(record)
    try:
        publish(tmp_path / "objects.ahobjects")
    finally:
        unsubscribe(token)
    assert len(observed) == 1
    assert observed[0].path == (tmp_path / "objects.ahobjects").resolve()
    assert observed[0].changed_object_ids == ()
    assert observed[0].deleted_object_ids == ()


def test_repository_change_carries_deleted_immutable_object_ids(tmp_path):
    observed = []
    def record(event):
        observed.append(event)
    token = subscribe(record)
    try:
        publish(
            tmp_path / "objects.ahobjects",
            changed_object_ids=("survivor",),
            deleted_object_ids=("root", "child"),
        )
    finally:
        unsubscribe(token)

    assert observed[0].changed_object_ids == ("survivor",)
    assert observed[0].deleted_object_ids == ("root", "child")


def test_repository_save_replaces_document_without_leaving_a_temporary_file(tmp_path):
    path = tmp_path / "objects.ahobjects"
    root = ComponentDefinition(
        component_id="root",
        object_id=str(uuid4()),
        strategies=(ComponentStrategy("atspi", {"identification": {"mandatory": {"name": "Root"}}}),),
    )
    repository = ComponentRepository({"root": root})

    repository.save(path)

    assert ComponentRepository.load((path,)).get("root").object_id == repository.get("root").object_id
    assert not tuple(tmp_path.glob(".objects.ahobjects.*.tmp"))
