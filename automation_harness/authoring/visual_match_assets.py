"""Import repository-owned images for explicit visual match assertions."""
from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path

from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.visual_baselines import DEFAULT_MAX_DIFFERENCE_RATIO, DEFAULT_PIXEL_TOLERANCE


def import_visual_match(repository_path: Path, component_id: str, source_image: Path, match_id: str):
    repository_path = Path(repository_path).resolve()
    source_image = Path(source_image).resolve()
    match_id = match_id.strip()
    if not match_id or not all(ch.isalnum() or ch in "-_" for ch in match_id):
        raise ValueError("match image ID must contain only letters, numbers, '-' or '_'")
    if source_image.suffix.casefold() != ".png" or not source_image.is_file():
        raise ValueError("match image must be an existing PNG file")
    repository = ComponentRepository.load([repository_path])
    definition = repository.get(component_id)
    relative = Path("visual") / "matches" / component_id.replace(".", "/") / (match_id + ".png")
    destination = repository_path.parent / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_image, destination)
    visual = dict(definition.visual or {"bounds": "component", "revision": 0, "variants": {}})
    variants = dict(visual.get("variants", {}))
    variants[match_id] = {
        "image": relative.as_posix(),
        "profile": {"purpose": "assert_match"},
        "component_revision": definition.revision,
        "pixel_tolerance": DEFAULT_PIXEL_TOLERANCE,
        "max_difference_ratio": DEFAULT_MAX_DIFFERENCE_RATIO,
    }
    visual.update({"bounds": "component", "revision": int(visual.get("revision", 0)) + 1, "variants": variants})
    updated = replace(definition, visual=visual, repository_path=repository_path)
    repository.with_component(updated).save(repository_path)
    return updated
