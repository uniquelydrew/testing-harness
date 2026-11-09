# Automation Harness Extension Format (`.ahext`) — v1

`.ahext` is a **standard ZIP archive** containing an authoritative `manifest.json`,
versioned declarative component/behavior specifications, and optional binary
assets (PNG/JPEG reference images, schemas, etc.). A custom suffix is a file
association only; **it is not an archive security mechanism**. Ordinary
`.ahobjects` and plan loaders must not interpret `.ahext` as a repository.

## Build, validate, inspect

From the repository root:

```bash
python3.12 -m automation_harness.extensions build extension_packs/solipsys /tmp/solipsys.ahext
python3.12 -m automation_harness.extensions build extension_packs/msct /tmp/msct.ahext
python3.12 -m automation_harness.extensions verify /tmp/solipsys.ahext /tmp/msct.ahext
python3.12 -m automation_harness.extensions inspect /tmp/msct.ahext
```

The builder sorts entries and fixes timestamps to produce reproducible archive
bytes. Its generated `files` map contains SHA-256 for every member except the
manifest itself. Source manifests do not have to maintain checksums manually.

## Manifest contract

```json
{
  "format": "automation-harness-extension",
  "format_version": 1,
  "extension": {
    "id": "org.automation-harness.msct",
    "name": "MSCT Components",
    "version": "0.1.0"
  },
  "requires": {"harness": ">=0.5.2,<0.6.0"},
  "dependencies": [
    {"id": "org.automation-harness.solipsys", "version": ">=0.1.0,<1.0.0"}
  ],
  "resources": {
    "components": ["components/messaging_status.json"],
    "behaviors": ["behaviors/label_value_reader.json"],
    "assets": [],
    "schemas": []
  },
  "adapters": [],
  "files": {
    "components/messaging_status.json": "sha256:<64 lowercase hex characters>"
  }
}
```

The `files` example uses a placeholder. Real `.ahext` manifests are generated
by the builder and carry actual digests. All referenced resources must exist
in `files`. Extra archive members not in `files` are rejected. Resource paths
are POSIX paths relative to the archive root; absolute paths, parent traversal,
backslashes, symlinks, duplicate members, encrypted entries, unsupported
compression and oversized payloads are rejected. Archive validation reads
and checksums each packaged member and parses declared JSON resources.

Format v1 supports JSON descriptors for `components`, `behaviors`, and
`schemas`. `assets` may be binary data, including reference PNG images. A
component descriptor has a dotted ID prefixed by its extension ID and an
`extends` value naming an existing canonical base component type. Unlike
an `ObjectType` enum value, this extension component ID is open-ended.

`adapters` may describe optional Python/Java implementations with fields `id`,
`runtime`, `entrypoint`, and `path`. **Nothing loads or executes these entries**
in format-v1 registry code. An adapter deployment, trust, authorization, and
sandbox/lifecycle contract will be designed separately. A SHA-256 checksum
checks integrity *within* the archive but is not a signature or proof of
publisher identity.

Only stable three-part numeric versions (`MAJOR.MINOR.PATCH`) are supported in
v1. Requirements accept comma-separated `==`, `!=`, `>=`, `<=`, `>` and `<`
comparisons. Unknown versions or constraint syntax fail validation rather than
being silently accepted.

## Runtime model

```python
from automation_harness.extensions import ExtensionRegistry

registry = ExtensionRegistry()
registry.register("/tmp/solipsys.ahext")
registry.register("/tmp/msct.ahext")
registry.dependency_order()  # compatibility, dependency and cycle checks
monitor = registry.get_component("org.automation-harness.msct.messaging_status")
```

A registry is read-only and opt-in. It does not globally mutate the canonical
component catalog or legacy `.ahobjects` definitions. The format makes
extension resources addressable; the subsequent integration phase will make
component type selection, action parameter authoring, status monitor snapshot
execution, and package reference persistence consume those definitions.

Images are referenced via `ahext://<extension-id>@<version>/<asset-path>`.
Only images declared under `resources.assets` can be read through the asset
URI API. Consumers must not assume that image matching is a fallback unless
it was explicitly configured in the component's resolution strategy.

## Application separation

`extension_packs/solipsys` provides declarative metadata for rendered-canvas
components. `extension_packs/msct` provides the status-monitor component model
and declares a dependency on the Solipsys package. The existing native
Solipsys adapter and capture/identity policies have **not** been relocated in
this phase. They remain operational until extension-native adapter registration
and backward-compatible object migration are qualified.

In particular, MSCT's menu-shaped status popup is **read-only data**, not a
menu-item selector. The monitor profile is a declaration for a future
label-to-value observer, not an implemented extraction engine. Assertions must
read fresh runtime snapshots, never historical capture metadata.
