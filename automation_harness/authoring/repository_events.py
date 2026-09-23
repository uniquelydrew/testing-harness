"""Process-local repository mutation notifications for open authoring windows."""
from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass
import weakref


_SUBSCRIBERS = []


@dataclass(frozen=True)
class RepositoryChange:
    """The single notification emitted after a repository reaches disk.

    UUIDs, rather than display names, let open plans identify calls that have
    become invalid after a subtree deletion.
    """

    path: Path
    changed_object_ids: tuple[str, ...] = ()
    deleted_object_ids: tuple[str, ...] = ()


def subscribe(callback):
    reference = weakref.WeakMethod(callback) if getattr(callback, "__self__", None) is not None else weakref.ref(callback)
    _SUBSCRIBERS.append(reference)
    return reference


def unsubscribe(reference):
    try:
        _SUBSCRIBERS.remove(reference)
    except ValueError:
        pass


def publish(path, *, changed_object_ids=(), deleted_object_ids=()):
    event = RepositoryChange(
        Path(path).resolve(),
        tuple(str(value) for value in changed_object_ids),
        tuple(str(value) for value in deleted_object_ids),
    )
    live = []
    for reference in tuple(_SUBSCRIBERS):
        callback = reference()
        if callback is None:
            continue
        live.append(reference)
        callback(event)
    _SUBSCRIBERS[:] = live
