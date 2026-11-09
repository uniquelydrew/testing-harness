"""Opt-in binding, version pinning, and authoring option contracts."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from automation_harness.extensions import (
    BINDING_KEY, ExtensionFormatError, ExtensionRegistry, available_profiles,
    bind_extension, build_extension, component_options,
    load_registry_from_environment, resolve_extension, unbind_extension,
)


@dataclass(frozen=True)
class _Type:
    value: str


@dataclass(frozen=True)
class _Definition:
    component_id: str = "Monitor"
    object_id: str = "fixed-immutable-uuid"
    object_type: _Type = _Type("panel")
    framework: str = "swing"
    strategies: tuple[str, ...] = ("original-locator",)
    actions: frozenset[str] = frozenset({"select_menu_item"})
    revision: int = 1
    properties: dict = field(default_factory=lambda: {"original": "kept"})


@pytest.fixture
def registry(tmp_path: Path) -> ExtensionRegistry:
    root = Path(__file__).resolve().parents[2] / "extension_packs"
    reg = ExtensionRegistry(harness_version="0.5.2")
    reg.register(build_extension(root / "msct", tmp_path / "msct.ahext").path)
    reg.register(build_extension(root / "solipsys", tmp_path / "solipsys.ahext").path)
    return reg


def test_opt_in_extension_binding_does_not_rewrite_existing_identity(registry):
    original = _Definition()
    available = available_profiles(original, registry)
    ids = [item["id"] for item in available]
    assert "org.automation-harness.msct.messaging_status" in ids
    assert "org.automation-harness.solipsys.rendered_track" not in ids
    bound = bind_extension(original, ids[0], registry)
    assert bound.object_id == original.object_id
    assert bound.object_type == original.object_type
    assert bound.strategies == original.strategies
    assert bound.actions == original.actions
    assert bound.revision == 2
    assert original.properties == {"original": "kept"}
    assert bound.properties[BINDING_KEY] == {"component_id": ids[0], "version": "0.1.0"}
    assert bind_extension(bound, ids[0], registry) is bound
    assert unbind_extension(bound).properties == original.properties
    assert unbind_extension(original) is original


def test_option_inventory_is_declarative_not_fake_observations(registry):
    definition = bind_extension(
        _Definition(), "org.automation-harness.msct.messaging_status", registry,
    )
    opts = component_options(definition, registry)
    assert opts["id"].endswith(".messaging_status")
    assert opts["read_only"] is True
    assert opts["fields"][0]["label"] == "Packets Received:"
    assert opts["observation"]["fresh_runtime_required"] is True
    assert "value" not in opts["fields"][0]
    assert resolve_extension(definition, registry)["interaction"]["selection"] is False


def test_wrong_base_rejected_and_missing_registry_fails_closed(registry):
    with pytest.raises(ExtensionFormatError, match="incompatible"):
        bind_extension(
            _Definition(object_type=_Type("button")),
            "org.automation-harness.msct.messaging_status", registry,
        )
    bound = bind_extension(_Definition(), "org.automation-harness.msct.process_status", registry)
    with pytest.raises(ExtensionFormatError, match="requires extension"):
        resolve_extension(bound, None)
    assert resolve_extension(_Definition(), None) is None


def test_invalid_or_mismatched_pin_never_falls_back(registry):
    bound = bind_extension(_Definition(), "org.automation-harness.msct.process_status", registry)
    from dataclasses import replace
    with pytest.raises(ExtensionFormatError, match="version mismatch"):
        resolve_extension(replace(bound, properties={BINDING_KEY: {
            "component_id": "org.automation-harness.msct.process_status",
            "version": "1.0.0",
        }}), registry)
    with pytest.raises(ExtensionFormatError, match="invalid component extension binding"):
        resolve_extension(replace(bound, properties={BINDING_KEY: {"version": "0.1.0"}}), registry)


def test_no_package_discovery_unless_explicitly_configured(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[2] / "extension_packs"
    msct = build_extension(root / "msct", tmp_path / "msct.ahext")
    solipsys = build_extension(root / "solipsys", tmp_path / "solipsys.ahext")
    monkeypatch.delenv("AUTOMATION_HARNESS_EXTENSIONS", raising=False)
    assert load_registry_from_environment(harness_version="0.5.2") is None
    monkeypatch.setenv("AUTOMATION_HARNESS_EXTENSIONS", str(msct.path))
    with pytest.raises(ExtensionFormatError, match="missing dependency"):
        load_registry_from_environment(harness_version="0.5.2")
    monkeypatch.setenv("AUTOMATION_HARNESS_EXTENSIONS", str(solipsys.path) + ":" + str(msct.path))
    registry = load_registry_from_environment(harness_version="0.5.2")
    assert registry.dependency_order() == (
        "org.automation-harness.solipsys", "org.automation-harness.msct",
    )
    monkeypatch.setenv("AUTOMATION_HARNESS_EXTENSIONS", str(msct.path) + ":")
    with pytest.raises(ExtensionFormatError, match="empty package path"):
        load_registry_from_environment(harness_version="0.5.2")


def test_extension_catalog_is_explicit_read_only_overlay(registry):
    from automation_harness.core.component_catalog import (
        canonical_action_types, component_spec, display_name,
    )
    from automation_harness.models.gui import ActionType

    base = component_spec("panel")
    assert ActionType.CLICK in canonical_action_types("panel")
    profile_id = "org.automation-harness.msct.messaging_status"
    with pytest.raises(ValueError, match="explicit extension registry"):
        component_spec(profile_id)
    resolved = component_spec(profile_id, registry=registry)
    assert resolved["base_type"] == "panel"
    assert resolved["container"] == base["container"]
    assert resolved["interaction"]["selection"] is False
    assert canonical_action_types(profile_id, registry=registry) == frozenset()
    assert display_name(profile_id, registry=registry) == "Messaging Status"
    assert component_spec("panel") == base  # no global mutation
