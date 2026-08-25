"""Portable component-bound visual baseline capture and approval."""
from __future__ import annotations

import platform
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from PIL import Image, ImageChops, ImageGrab

from automation_harness.core.component_repository import ComponentRepository
from automation_harness.models.component import ComponentDefinition

DEFAULT_PIXEL_TOLERANCE = 12
DEFAULT_MAX_DIFFERENCE_RATIO = 0.01


class VisualBaselineError(RuntimeError):
    pass


@dataclass(frozen=True)
class VisualProfile:
    values: Mapping[str, str]

    @classmethod
    def current(cls, override: Mapping[str, str] | None = None) -> "VisualProfile":
        values = {
            "os": platform.system().lower() or "unknown",
            "arch": platform.machine().lower() or "unknown",
            "scale": "100",
            "color_scheme": "unknown",
            "session": _session_name(),
        }
        if override:
            values.update({str(k): str(v) for k, v in override.items()})
        return cls(values)

    @property
    def key(self) -> str:
        return "-".join(_safe(self.values.get(name, "unknown")) for name in ("os", "arch", "scale", "color_scheme", "session"))


def _session_name() -> str:
    import os
    return (os.environ.get("XDG_SESSION_TYPE") or os.environ.get("SESSIONNAME") or "default").lower()


def _safe(value: str) -> str:
    result = "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")
    return result or "unknown"


def _component_path(component_id: str) -> Path:
    return Path(*(_safe(part) for part in component_id.split(".")))


def _repository_root(path: Path) -> Path:
    return path.resolve().parent


def _asset_path(repository: Path, relative: str) -> Path:
    root = _repository_root(repository)
    result = (root / relative).resolve()
    visual_root = (root / "visual").resolve()
    if visual_root not in result.parents:
        raise VisualBaselineError("visual asset path escapes repository visual directory")
    return result


def stage_visual_candidate(
    repository_path: Path,
    definition: ComponentDefinition,
    bounds: tuple[int, int, int, int],
    *,
    profile: VisualProfile | None = None,
    pixel_tolerance: int = DEFAULT_PIXEL_TOLERANCE,
    max_difference_ratio: float = DEFAULT_MAX_DIFFERENCE_RATIO,
    image: Image.Image | None = None,
) -> dict[str, Any]:
    x, y, width, height = bounds
    if width <= 0 or height <= 0:
        raise VisualBaselineError("visual capture requires positive component bounds")
    if pixel_tolerance < 0 or not 0 <= max_difference_ratio <= 1:
        raise VisualBaselineError("invalid visual comparison tolerance")
    profile = profile or VisualProfile.current()
    candidate = image.convert("RGB") if image is not None else ImageGrab.grab().convert("RGB").crop((x, y, x + width, y + height))
    if candidate.size != (width, height):
        raise VisualBaselineError("supplied visual candidate does not match component bounds")
    stage_dir = _repository_root(repository_path) / "visual" / ".staging" / _component_path(definition.component_id) / profile.key
    stage_dir.mkdir(parents=True, exist_ok=True)
    candidate_path = stage_dir / "candidate.png"
    candidate.save(candidate_path)
    (stage_dir / "metadata.json").write_text(json.dumps({
        "profile": dict(profile.values), "pixel_tolerance": pixel_tolerance,
        "max_difference_ratio": max_difference_ratio,
    }, sort_keys=True), encoding="utf-8")
    baseline = _select_variant(definition, profile)
    result: dict[str, Any] = {
        "component_id": definition.component_id, "variant_key": profile.key, "profile": dict(profile.values),
        "candidate": str(candidate_path), "bounds": list(bounds), "pixel_tolerance": pixel_tolerance,
        "max_difference_ratio": max_difference_ratio,
    }
    if baseline:
        approved = _asset_path(repository_path, baseline["image"])
        if approved.is_file():
            previous_path = stage_dir / "approved.png"
            shutil.copy2(approved, previous_path)
            expected = Image.open(approved).convert("RGB")
            if expected.size != candidate.size:
                raise VisualBaselineError("approved baseline size does not match candidate bounds")
            ImageChops.difference(candidate, expected).save(stage_dir / "diff.png")
            result.update({"approved": str(previous_path), "diff": str(stage_dir / "diff.png")})
    return result


def approve_visual_candidate(repository_path: Path, component_id: str, variant_key: str, *, mask: Path | None = None) -> ComponentDefinition:
    repository = ComponentRepository.load([repository_path])
    definition = repository.get(component_id)
    stage_dir = _repository_root(repository_path) / "visual" / ".staging" / _component_path(component_id) / variant_key
    candidate = stage_dir / "candidate.png"
    if not candidate.is_file():
        raise VisualBaselineError(f"no staged visual candidate for {component_id!r} variant {variant_key!r}")
    with Image.open(candidate) as source:
        size = source.size
    if mask is not None:
        with Image.open(mask) as mask_image:
            if mask_image.size != size:
                raise VisualBaselineError("visual mask size does not match candidate")
    profile = _profile_from_key_or_stage(stage_dir, variant_key)
    rel_image = (Path("visual") / _component_path(component_id) / f"{variant_key}.png").as_posix()
    image_path = _asset_path(repository_path, rel_image)
    image_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = image_path.with_suffix(".tmp")
    shutil.copy2(candidate, temporary)
    temporary.replace(image_path)
    visual = dict(definition.visual or {"bounds": "component", "revision": 0, "variants": {}})
    variants = dict(visual.get("variants", {}))
    item: dict[str, Any] = {
        "image": rel_image, "profile": profile.values, "component_revision": definition.revision,
        "pixel_tolerance": _stage_number(stage_dir, "pixel_tolerance", DEFAULT_PIXEL_TOLERANCE),
        "max_difference_ratio": _stage_number(stage_dir, "max_difference_ratio", DEFAULT_MAX_DIFFERENCE_RATIO),
    }
    if mask is not None:
        rel_mask = (Path("visual") / _component_path(component_id) / f"{variant_key}.mask.png").as_posix()
        mask_path = _asset_path(repository_path, rel_mask)
        shutil.copy2(mask, mask_path)
        item["mask"] = rel_mask
    elif variant_key in variants and variants[variant_key].get("mask"):
        item["mask"] = variants[variant_key]["mask"]
    variants[variant_key] = item
    visual.update({"bounds": "component", "revision": int(visual.get("revision", 0)) + 1, "variants": variants})
    from dataclasses import replace
    updated = replace(definition, visual=visual, repository_path=repository_path)
    repository.with_component(updated).save(repository_path)
    return updated


def reject_visual_candidate(repository_path: Path, component_id: str, variant_key: str) -> None:
    stage_dir = _repository_root(repository_path) / "visual" / ".staging" / _component_path(component_id) / variant_key
    if not stage_dir.is_dir():
        raise VisualBaselineError(f"no staged visual candidate for {component_id!r} variant {variant_key!r}")
    shutil.rmtree(stage_dir)


def _stage_number(stage_dir: Path, key: str, default: float | int) -> float | int:
    metadata = _stage_metadata(stage_dir)
    return metadata.get(key, default)


def _profile_from_key_or_stage(stage_dir: Path, key: str) -> VisualProfile:
    profile = _stage_metadata(stage_dir).get("profile")
    if isinstance(profile, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in profile.items()):
        return VisualProfile(profile)
    raise VisualBaselineError(f"staged candidate {key!r} is missing valid profile metadata")


def _stage_metadata(stage_dir: Path) -> dict[str, Any]:
    try:
        raw = json.loads((stage_dir / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise VisualBaselineError("staged candidate metadata is missing or invalid") from exc
    if not isinstance(raw, dict):
        raise VisualBaselineError("staged candidate metadata is invalid")
    return raw


def _select_variant(definition: ComponentDefinition, profile: VisualProfile) -> Mapping[str, Any] | None:
    visual = definition.visual or {}
    return visual.get("variants", {}).get(profile.key)


def select_visual_variant(definition: ComponentDefinition, profile: VisualProfile | None = None) -> tuple[str, Mapping[str, Any]]:
    profile = profile or VisualProfile.current()
    variant = _select_variant(definition, profile)
    if variant is None:
        available = ", ".join(sorted((definition.visual or {}).get("variants", {}))) or "none"
        raise VisualBaselineError(f"no approved visual baseline for {definition.component_id!r} variant {profile.key!r}; available: {available}")
    return profile.key, variant
