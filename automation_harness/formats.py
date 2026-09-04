"""Distinct, YAML-backed file names for authoring artifacts."""
from __future__ import annotations

from pathlib import Path


PROJECT_SUFFIX = ".ahproject"
PLAN_SUFFIX = ".ahplan"
REPOSITORY_SUFFIX = ".ahobjects"
SCRIPT_STEP_SUFFIX = ".ahstep"


def with_artifact_suffix(path: Path, suffix: str) -> Path:
    """Append an artifact suffix when a save target has no recognized YAML suffix.

    Existing ``.yaml``/``.yml`` names remain loadable for backwards compatibility;
    chooser-driven saves replace that generic suffix with the artifact suffix.
    """
    name = path.name.casefold()
    if name.endswith(suffix.casefold()):
        return path
    if name.endswith(".yaml"):
        return path.with_name(path.name[:-5] + suffix)
    if name.endswith(".yml"):
        return path.with_name(path.name[:-4] + suffix)
    return path.with_name(path.name + suffix)


def artifact_stem(path: Path, suffix: str) -> str:
    """Return a human-facing name without exposing the artifact extension."""
    return path.name[:-len(suffix)] if path.name.casefold().endswith(suffix.casefold()) else path.stem
