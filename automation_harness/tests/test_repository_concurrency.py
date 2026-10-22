"""Optimistic repository save safeguards for parallel authoring sessions."""
from pathlib import Path
from uuid import uuid4

import pytest

from automation_harness.core.component_repository import (
    ComponentRepository,
    ComponentRepositoryError,
    repository_fingerprint,
)


def _repository(label):
    return ComponentRepository.from_document({
        "version": 3,
        "components": {
            label: {
                "object_id": str(uuid4()),
                "object_type": "button",
                "strategies": [{"type": "atspi", "identification": {"mandatory": {"name": label}}}],
            },
        },
    })


def test_save_rejects_external_change_and_preserves_disk_and_in_memory_state(tmp_path: Path):
    path = tmp_path / "objects.ahobjects"
    _repository("Original").save(path)
    opened_fingerprint = repository_fingerprint(path)
    staged = _repository("Unsaved Edit")
    external = _repository("Other Editor")
    external.save(path)
    existing_content = path.read_bytes()

    with pytest.raises(ComponentRepositoryError, match="changed on disk"):
        staged.save(path, expected_fingerprint=opened_fingerprint)

    assert path.read_bytes() == existing_content
    assert staged.contains("Unsaved Edit")
    assert ComponentRepository.load((path,)).contains("Other Editor")
    assert not tuple(tmp_path.glob(".*.tmp"))


def test_save_with_matching_fingerprint_updates_baseline(tmp_path: Path):
    path = tmp_path / "objects.ahobjects"
    initial = repository_fingerprint(path)
    assert initial.exists is False
    first = _repository("First")
    committed = first.save(path, expected_fingerprint=initial)
    assert committed == repository_fingerprint(path)
    assert committed.exists is True

    second = _repository("Second")
    next_version = second.save(path, expected_fingerprint=committed)
    assert next_version == repository_fingerprint(path)
    assert next_version != committed
    assert ComponentRepository.load((path,)).contains("Second")


def test_save_rejects_unexpected_replacement_of_missing_file(tmp_path: Path):
    path = tmp_path / "objects.ahobjects"
    initial = repository_fingerprint(path)
    _repository("Other Editor").save(path)
    before = path.read_bytes()

    with pytest.raises(ComponentRepositoryError, match="changed on disk"):
        _repository("My Edit").save(path, expected_fingerprint=initial)

    assert path.read_bytes() == before
