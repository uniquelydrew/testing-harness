"""Package-format and registry regressions; no native display or Java agent needed."""
from __future__ import annotations

import hashlib
import json
import stat
import warnings
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest

from automation_harness.extensions import (
    ExtensionFormatError, ExtensionPackage, ExtensionRegistry,
    build_extension, matches_version,
)


EXT_ID = "org.example.monitor"


def _manifest(ext_id: str = EXT_ID, *, dependencies: list[dict] | None = None) -> dict:
    return {
        "format": "automation-harness-extension",
        "format_version": 1,
        "extension": {"id": ext_id, "name": "Monitor Example", "version": "0.1.0"},
        "requires": {"harness": ">=0.5.2,<0.6.0"},
        "dependencies": dependencies or [],
        "resources": {
            "components": ["components/monitor.json"],
            "behaviors": ["behaviors/read.json"],
            "assets": ["assets/reference.png"],
            "schemas": [],
        },
        "adapters": [],
    }


def _source(directory: Path, *, ext_id: str = EXT_ID, dependencies=None) -> Path:
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_text(
        json.dumps(_manifest(ext_id, dependencies=dependencies)), encoding="utf-8",
    )
    for relative, payload in {
        "components/monitor.json": {
            "id": ext_id + ".monitor", "extends": "label",
            "capabilities": ["inspect", "read_fields"],
        },
        "behaviors/read.json": {
            "id": ext_id + ".read", "kind": "observation", "execution_state": "declarative_only",
        },
    }.items():
        file = directory / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(payload), encoding="utf-8")
    asset = directory / "assets/reference.png"
    asset.parent.mkdir(parents=True, exist_ok=True)
    asset.write_bytes(b"\x89PNG\r\n\x1a\nsynthetic-fixture")
    return directory


def _bundle(tmp_path: Path, *, ext_id: str = EXT_ID, dependencies=None) -> ExtensionPackage:
    source = _source(tmp_path / "source", ext_id=ext_id, dependencies=dependencies)
    return build_extension(source, tmp_path / "pack.ahext")


def _rewrite_archive(path: Path, *, entries: dict[str, bytes], filenames: list[str] | None = None) -> None:
    with ZipFile(path, "w") as output:
        for name in (filenames or list(entries)):
            output.writestr(name, entries[name], compress_type=ZIP_DEFLATED)


def _entries(path: Path) -> dict[str, bytes]:
    with ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def test_build_and_validate_bytes_and_content(tmp_path):
    package = _bundle(tmp_path)
    assert package.extension_id == EXT_ID
    assert package.version == "0.1.0"
    assert package.read_json("components/monitor.json")["extends"] == "label"
    assert package.read_bytes("assets/reference.png").startswith(b"\x89PNG")
    assert package.manifest["files"]["assets/reference.png"] == (
        "sha256:" + hashlib.sha256(package.read_bytes("assets/reference.png")).hexdigest()
    )
    assert package.asset_uri("assets/reference.png") == (
        "ahext://org.example.monitor@0.1.0/assets/reference.png"
    )
    with pytest.raises(ExtensionFormatError, match="not a declared package asset"):
        package.asset_uri("components/monitor.json")


def test_build_is_byte_for_byte_deterministic(tmp_path):
    source = _source(tmp_path / "source")
    a = build_extension(source, tmp_path / "a.ahext")
    b = build_extension(source, tmp_path / "b.ahext")
    assert a.path.read_bytes() == b.path.read_bytes()


def test_cannot_build_inside_source_or_use_wrong_suffix(tmp_path):
    source = _source(tmp_path / "source")
    with pytest.raises(ExtensionFormatError, match="outside"):
        build_extension(source, source / "inside.ahext")
    with pytest.raises(ExtensionFormatError, match="must end"):
        build_extension(source, tmp_path / "pack.zip")


def test_invalid_checksum_is_rejected(tmp_path):
    package = _bundle(tmp_path)
    entries = _entries(package.path)
    entries["assets/reference.png"] = b"tampered"
    _rewrite_archive(package.path, entries=entries)
    with pytest.raises(ExtensionFormatError, match="checksum mismatch"):
        ExtensionPackage.open(package.path)


def test_undeclared_archive_entry_is_rejected(tmp_path):
    package = _bundle(tmp_path)
    entries = _entries(package.path)
    entries["unlisted.txt"] = b"surprise"
    _rewrite_archive(package.path, entries=entries)
    with pytest.raises(ExtensionFormatError, match="exactly match"):
        ExtensionPackage.open(package.path)


@pytest.mark.parametrize("bad_path", ["../outside", "/absolute", "a/../../b", "a\\b", "a//b", "C:drive"])
def test_archive_path_traversal_and_invalid_separators_are_rejected(tmp_path, bad_path):
    package = _bundle(tmp_path)
    entries = _entries(package.path)
    entries[bad_path] = b"bad"
    _rewrite_archive(package.path, entries=entries)
    with pytest.raises(ExtensionFormatError, match="archive path"):
        ExtensionPackage.open(package.path)


def test_archive_symlink_is_rejected(tmp_path):
    package = _bundle(tmp_path)
    entries = _entries(package.path)
    with ZipFile(package.path, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
        info = ZipInfo("link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "../../private")
    with pytest.raises(ExtensionFormatError, match="symbolic links"):
        ExtensionPackage.open(package.path)


def test_duplicate_archive_entries_are_rejected(tmp_path):
    package = _bundle(tmp_path)
    entries = _entries(package.path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with ZipFile(package.path, "w") as archive:
            for name, content in entries.items():
                archive.writestr(name, content)
            archive.writestr("assets/reference.png", b"duplicate")
    with pytest.raises(ExtensionFormatError, match="duplicate archive path"):
        ExtensionPackage.open(package.path)


def test_build_refuses_symlink_source(tmp_path):
    source = _source(tmp_path / "source")
    (source / "linked.txt").symlink_to(source / "manifest.json")
    with pytest.raises(ExtensionFormatError, match="symbolic links"):
        build_extension(source, tmp_path / "pack.ahext")


def test_declarative_adapter_never_imported_or_executed(tmp_path):
    source = _source(tmp_path / "source")
    marker = tmp_path / "executed"
    adapter = source / "adapters" / "boom.py"
    adapter.parent.mkdir()
    adapter.write_text("from pathlib import Path\nPath(%r).write_text('bad')\n" % str(marker))
    manifest = json.loads((source / "manifest.json").read_text())
    manifest["adapters"] = [
        {"id": EXT_ID + ".boom", "runtime": "python", "entrypoint": "boom:Adapter", "path": "adapters/boom.py"},
    ]
    (source / "manifest.json").write_text(json.dumps(manifest))
    package = build_extension(source, tmp_path / "pack.ahext")
    registry = ExtensionRegistry(harness_version="0.5.2")
    registry.register(package.path)
    assert not marker.exists()


def test_registry_loads_declarative_components_and_assets(tmp_path):
    package = _bundle(tmp_path)
    registry = ExtensionRegistry(harness_version="0.5.2")
    registry.register(package.path)
    assert registry.dependency_order() == (EXT_ID,)
    component = registry.get_component(EXT_ID + ".monitor")
    component["extends"] = "menu"
    assert registry.get_component(EXT_ID + ".monitor")["extends"] == "label"
    assert registry.get_behavior(EXT_ID + ".read")["kind"] == "observation"
    assert registry.read_asset(package.asset_uri("assets/reference.png")) == (
        package.read_bytes("assets/reference.png")
    )
    with pytest.raises(ExtensionFormatError, match="unavailable extension version"):
        registry.read_asset("ahext://org.example.monitor@9.9.9/assets/reference.png")


def test_duplicate_extension_and_component_rejected(tmp_path):
    package = _bundle(tmp_path)
    registry = ExtensionRegistry(harness_version="0.5.2")
    registry.register(package.path)
    with pytest.raises(ExtensionFormatError, match="already registered"):
        registry.register(package.path)


def test_missing_dependency_is_detected(tmp_path):
    dependency = {"id": "org.example.native", "version": ">=0.1.0,<1.0.0"}
    package = _bundle(tmp_path, dependencies=[dependency])
    registry = ExtensionRegistry(harness_version="0.5.2")
    registry.register(package.path)
    with pytest.raises(ExtensionFormatError, match="missing dependency"):
        registry.dependency_order()


def test_registry_resolves_dependency_order_and_version(tmp_path):
    left = _bundle(tmp_path / "left", ext_id="org.example.native")
    right = _bundle(
        tmp_path / "right", dependencies=[
            {"id": "org.example.native", "version": ">=0.1.0,<1.0.0"},
        ],
    )
    registry = ExtensionRegistry(harness_version="0.5.2")
    registry.register(right.path)
    registry.register(left.path)
    assert registry.dependency_order() == ("org.example.native", EXT_ID)


def test_incompatible_harness_version_rejected(tmp_path):
    package = _bundle(tmp_path)
    registry = ExtensionRegistry(harness_version="0.6.0")
    with pytest.raises(ExtensionFormatError, match="requires harness"):
        registry.register(package.path)


def test_stable_version_specifier_contract():
    assert matches_version("0.5.2", ">=0.5.2,<0.6.0")
    assert not matches_version("0.6.0", ">=0.5.2,<0.6.0")
    assert matches_version("1.0.0", "==1.0.0")
    assert not matches_version("1.0.0", "!=1.0.0")
    with pytest.raises(ExtensionFormatError, match="unsupported version requirement"):
        matches_version("1.0.0", ">=1.0")
    with pytest.raises(ExtensionFormatError, match="MAJOR.MINOR.PATCH"):
        matches_version("1.0.0rc1", ">=1.0.0")


def test_extension_pack_examples_can_build_and_resolve_dependencies(tmp_path):
    root = Path(__file__).resolve().parents[2]
    solipsys = build_extension(root / "extension_packs" / "solipsys", tmp_path / "solipsys.ahext")
    msct = build_extension(root / "extension_packs" / "msct", tmp_path / "msct.ahext")
    registry = ExtensionRegistry(harness_version="0.5.2")
    registry.register(msct.path)
    registry.register(solipsys.path)
    assert registry.dependency_order() == (
        "org.automation-harness.solipsys", "org.automation-harness.msct",
    )
    monitor = registry.get_component("org.automation-harness.msct.messaging_status")
    assert monitor["capabilities"] == ["inspect", "read_fields", "assert_fields"]
    assert monitor["observation"]["fresh_runtime_required"] is True


def test_corrupted_zip_is_rejected_as_format_error(tmp_path):
    invalid = tmp_path / "invalid.ahext"
    invalid.write_bytes(b"not a ZIP archive")
    with pytest.raises(ExtensionFormatError, match="invalid extension archive"):
        ExtensionPackage.open(invalid)
