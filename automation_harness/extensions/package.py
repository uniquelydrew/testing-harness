"""Versioned, data-only .ahext archives and deterministic package building.

A package is a ZIP container with a JSON manifest and SHA-256-digested files.
Reading a package never extracts or executes content. SHA-256 checks integrity,
not publisher authenticity; executable adapters require a separate trust design.
"""
from __future__ import annotations

import hashlib
import json
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, LargeZipFile, ZipFile, ZipInfo


FORMAT = "automation-harness-extension"
FORMAT_VERSION = 1
SUFFIX = ".ahext"
MANIFEST = "manifest.json"
MAX_FILES = 512
MAX_FILE_BYTES = 24 * 1024 * 1024
MAX_TOTAL_BYTES = 96 * 1024 * 1024
MAX_COMPRESSION_RATIO = 500
_ID = re.compile(r"[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)+")
_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
_SHA = re.compile(r"sha256:[a-f0-9]{64}")
_SPEC = re.compile(r"(==|!=|>=|<=|>|<)(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
_RESOURCE_KINDS = ("components", "behaviors", "assets", "schemas")


class ExtensionFormatError(ValueError):
    """The archive or its manifest does not satisfy the format contract."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ExtensionFormatError(message)


def _relative_path(value: Any) -> str:
    _require(isinstance(value, str) and bool(value), "archive paths must be nonempty strings")
    _require("\\" not in value and ":" not in value, "archive paths must use relative POSIX paths")
    _require(not any(ord(char) < 32 for char in value), "archive path contains a control character")
    path = PurePosixPath(value)
    _require(
        not path.is_absolute() and value == path.as_posix()
        and all(part not in {"", ".", ".."} for part in value.split("/")),
        "invalid archive path: %r" % value,
    )
    return value


def _identifier(value: Any) -> str:
    _require(isinstance(value, str) and _ID.fullmatch(value) is not None,
             "extension/component id must be a lower-case dotted identifier")
    return value


def _version(value: Any) -> tuple[int, int, int]:
    _require(isinstance(value, str) and _VERSION.fullmatch(value) is not None,
             "version must be stable MAJOR.MINOR.PATCH")
    return tuple(int(part) for part in value.split("."))  # type: ignore[return-value]


def matches_version(version: str, requirement: str) -> bool:
    """Evaluate a deliberately small, explicit stable-SemVer specifier subset.

    Comma-joined constraints are conjunctive; prerelease and wildcards are not
    accepted in format v1 rather than being silently miscompared.
    """
    actual = _version(version)
    _require(isinstance(requirement, str) and bool(requirement.strip()),
             "version requirement must not be empty")
    for raw in requirement.split(","):
        constraint = raw.strip()
        match = _SPEC.fullmatch(constraint)
        _require(match is not None, "unsupported version requirement: %r" % constraint)
        target = tuple(int(match.group(i)) for i in (2, 3, 4))
        operator = match.group(1)
        comparison = (actual > target) - (actual < target)
        if not {
            "==": comparison == 0, "!=": comparison != 0,
            ">=": comparison >= 0, "<=": comparison <= 0,
            ">": comparison > 0, "<": comparison < 0,
        }[operator]:
            return False
    return True


def _manifest(raw: Any) -> dict[str, Any]:
    _require(isinstance(raw, dict), "manifest must be a JSON object")
    allowed = {"format", "format_version", "extension", "requires", "dependencies", "resources", "adapters", "files", "metadata"}
    _require(set(raw) <= allowed, "unknown manifest keys: %s" % sorted(set(raw) - allowed))
    _require(raw.get("format") == FORMAT, "unsupported extension format")
    _require(type(raw.get("format_version")) is int and raw["format_version"] == FORMAT_VERSION,
             "unsupported extension format version")
    extension = raw.get("extension")
    _require(isinstance(extension, dict), "manifest.extension must be an object")
    _require(set(extension) == {"id", "name", "version"},
             "manifest.extension requires exactly id, name, version")
    _identifier(extension["id"])
    _version(extension["version"])
    _require(isinstance(extension["name"], str) and bool(extension["name"].strip()),
             "extension name must be nonempty")

    requires = raw.get("requires", {})
    _require(isinstance(requires, dict) and set(requires) <= {"harness"},
             "requires must be an object containing only harness")
    if "harness" in requires:
        matches_version("0.0.0", requires["harness"])  # validate syntax without enforcing it

    dependencies = raw.get("dependencies", [])
    _require(isinstance(dependencies, list), "dependencies must be a list")
    seen: set[str] = set()
    for dependency in dependencies:
        _require(isinstance(dependency, dict) and set(dependency) == {"id", "version"},
                 "dependencies require id and version")
        dep_id = _identifier(dependency["id"])
        _require(dep_id not in seen and dep_id != extension["id"],
                 "duplicate or self-referential dependency: %s" % dep_id)
        seen.add(dep_id)
        matches_version("0.0.0", dependency["version"])

    resources = raw.get("resources")
    _require(isinstance(resources, dict) and set(resources) <= set(_RESOURCE_KINDS),
             "resources must map known resource categories to path lists")
    listed: set[str] = set()
    for kind, paths in resources.items():
        _require(isinstance(paths, list), "resources.%s must be a list" % kind)
        for path in paths:
            path = _relative_path(path)
            _require(path != MANIFEST and path not in listed,
                     "resource path is duplicate or reserved: %s" % path)
            if kind in {"components", "behaviors", "schemas"}:
                _require(path.endswith(".json"), "%s resources require JSON files" % kind)
            listed.add(path)

    adapters = raw.get("adapters", [])
    _require(isinstance(adapters, list), "adapters must be a list")
    adapter_ids: set[str] = set()
    for adapter in adapters:
        _require(isinstance(adapter, dict) and set(adapter) == {"id", "runtime", "entrypoint", "path"},
                 "adapters require id, runtime, entrypoint and path")
        adapter_id = _identifier(adapter["id"])
        _require(adapter_id not in adapter_ids, "duplicate adapter id: %s" % adapter_id)
        adapter_ids.add(adapter_id)
        _require(adapter["runtime"] in {"python", "java"}, "unsupported adapter runtime")
        _require(isinstance(adapter["entrypoint"], str) and bool(adapter["entrypoint"].strip()),
                 "adapter entrypoint must be nonempty")
        listed.add(_relative_path(adapter["path"]))

    files = raw.get("files")
    _require(isinstance(files, dict), "manifest.files must contain SHA-256 records")
    for path, digest in files.items():
        _relative_path(path)
        _require(path != MANIFEST, "manifest cannot include its own digest")
        _require(isinstance(digest, str) and _SHA.fullmatch(digest) is not None,
                 "invalid SHA-256 digest for %s" % path)
    _require(listed <= set(files), "referenced resources not packaged: %s" % sorted(listed - set(files)))
    if "metadata" in raw:
        _require(isinstance(raw["metadata"], dict), "metadata must be an object")
    return raw


def _check_archive_infos(infos: list[ZipInfo]) -> dict[str, ZipInfo]:
    _require(len(infos) <= MAX_FILES, "too many archive entries")
    seen: dict[str, ZipInfo] = {}
    total = 0
    for info in infos:
        path = _relative_path(info.filename.rstrip("/") if info.is_dir() else info.filename)
        _require(path not in seen, "duplicate archive path: %s" % path)
        _require(not info.flag_bits & 1, "encrypted archive entries are not supported")
        _require(info.compress_type in {ZIP_STORED, ZIP_DEFLATED}, "unsupported ZIP compression")
        mode = info.external_attr >> 16
        _require(not stat.S_ISLNK(mode), "symbolic links are prohibited: %s" % path)
        if info.is_dir():
            continue
        _require(info.file_size <= MAX_FILE_BYTES, "archive member exceeds size limit: %s" % path)
        total += info.file_size
        _require(total <= MAX_TOTAL_BYTES, "archive exceeds total uncompressed size limit")
        _require(info.file_size <= MAX_COMPRESSION_RATIO * max(info.compress_size, 1),
                 "archive member exceeds compression ratio limit: %s" % path)
        seen[path] = info
    return seen


@dataclass(frozen=True)
class ExtensionPackage:
    """Validated archive; content is immutable, read on demand and never extracted."""

    path: Path
    manifest: Mapping[str, Any]

    @classmethod
    def open(cls, source: str | Path) -> ExtensionPackage:
        path = Path(source)
        _require(path.suffix == SUFFIX and path.is_file(), "expected an existing .ahext archive")
        try:
            with ZipFile(path) as archive:
                infos = _check_archive_infos(archive.infolist())
                _require(MANIFEST in infos, "manifest.json missing from archive root")
                manifest_bytes = archive.read(MANIFEST)
                _require(len(manifest_bytes) <= 1024 * 1024, "manifest is too large")
                manifest = _manifest(json.loads(manifest_bytes.decode("utf-8")))
                _require(set(infos) == {MANIFEST, *manifest["files"]},
                         "archive entries must exactly match manifest.files")
                for name, expected in manifest["files"].items():
                    digest = hashlib.sha256(archive.read(name)).hexdigest()
                    _require("sha256:" + digest == expected, "checksum mismatch: %s" % name)
                for name in ("components", "behaviors", "schemas"):
                    for resource in manifest["resources"].get(name, []):
                        data = json.loads(archive.read(resource).decode("utf-8"))
                        _require(isinstance(data, dict), "JSON resource must be an object: %s" % resource)
        except (OSError, ValueError, UnicodeError, KeyError, RuntimeError, BadZipFile, LargeZipFile) as exc:
            if isinstance(exc, ExtensionFormatError):
                raise
            raise ExtensionFormatError("invalid extension archive: %s" % exc) from exc
        return cls(path, manifest)

    @property
    def extension_id(self) -> str:
        return str(self.manifest["extension"]["id"])

    @property
    def version(self) -> str:
        return str(self.manifest["extension"]["version"])

    def read_bytes(self, name: str) -> bytes:
        _relative_path(name)
        _require(name in self.manifest["files"], "undeclared package resource: %s" % name)
        with ZipFile(self.path) as archive:
            contents = archive.read(name)
        _require(hashlib.sha256(contents).hexdigest() == self.manifest["files"][name][7:],
                 "resource changed since validation: %s" % name)
        return contents

    def read_json(self, name: str) -> dict[str, Any]:
        _require(name.endswith(".json"), "expected JSON resource")
        value = json.loads(self.read_bytes(name).decode("utf-8"))
        _require(isinstance(value, dict), "JSON resource must be an object")
        return value

    def asset_uri(self, name: str) -> str:
        _require(name in self.manifest["resources"].get("assets", []),
                 "resource is not a declared package asset")
        return "ahext://%s@%s/%s" % (self.extension_id, self.version, name)


def build_extension(source: str | Path, destination: str | Path) -> ExtensionPackage:
    """Build a reproducible .ahext from a directory containing manifest.json.

    Generated digests replace any source manifest.files mapping. Build writes to
    an atomic temporary file, verifies it, and then publishes the destination.
    """
    root = Path(source).resolve()
    target = Path(destination).resolve()
    _require(root.is_dir(), "extension source directory does not exist")
    _require(target.suffix == SUFFIX, "destination must end in .ahext")
    _require(root not in target.parents, "destination must be outside the extension source directory")
    raw = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    _require(isinstance(raw, dict), "source manifest must be a JSON object")
    files: dict[str, bytes] = {}
    count = 0
    total = 0
    for path in sorted(root.rglob("*")):
        _require(not path.is_symlink(), "extension sources cannot contain symbolic links")
        if path.is_dir():
            continue
        _require(path.is_file(), "nonregular package source: %s" % path)
        rel = _relative_path(path.relative_to(root).as_posix())
        if rel == MANIFEST:
            continue
        count += 1
        _require(count + 1 <= MAX_FILES, "too many package files")
        size = path.stat().st_size
        _require(size <= MAX_FILE_BYTES, "package file too large: %s" % rel)
        total += size
        _require(total <= MAX_TOTAL_BYTES, "package exceeds total size limit")
        files[rel] = path.read_bytes()
    raw["files"] = {
        name: "sha256:" + hashlib.sha256(contents).hexdigest()
        for name, contents in files.items()
    }
    manifest = _manifest(raw)
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    _require(len(manifest_bytes) <= 1024 * 1024, "manifest is too large")
    target.parent.mkdir(parents=True, exist_ok=True)
    staged: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".ahext-", suffix=SUFFIX, delete=False) as tmp:
            staged = Path(tmp.name)
        with ZipFile(staged, "w") as archive:
            for name, contents in [(MANIFEST, manifest_bytes), *sorted(files.items())]:
                info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, contents, compress_type=ZIP_DEFLATED, compresslevel=9)
        ExtensionPackage.open(staged)
        staged.replace(target)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)
    return ExtensionPackage.open(target)
