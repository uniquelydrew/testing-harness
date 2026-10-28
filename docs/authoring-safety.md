# Repository authoring safety

This document covers the combined object identity and authoring safeguards on
`codex/object-identity-authoring-safety-integration`. Canonical UUID
identification and repository preparation coexist with optimistic save
protection, recapture validation, and guarded graph reconciliation.

## Stale-save protection

Object Repository and Repository Identity Workbench editors fingerprint the
repository file when it opens. Save compares the current on-disk bytes against
that fingerprint before replacing the file. An external modification, including
the creation of a file that did not exist when opened, blocks the save with
a diagnostic rather than silently overwriting another editor's changes.

The editor retains its in-memory changes and remains dirty when saving fails.
Reload or reopen the artifact to review the newer version before applying
the changes again. An explicit repository API is also available:

```python
from pathlib import Path
from automation_harness.core.component_repository import (
    ComponentRepository, repository_fingerprint,
)

path = Path("/path/to/repository.ahobjects")
baseline = repository_fingerprint(path)
repository = ComponentRepository.load((path,))
# ... stage desired authoring edits ...
baseline = repository.save(path, expected_fingerprint=baseline)
```

Callers that omit `expected_fingerprint` retain the existing unconditional
save behavior. File fingerprints provide **optimistic** conflict detection,
not a cross-process atomic transaction: an uncooperative writer may still
change the file between the comparison and replacement.

## Safe recapture

The Object Identity Workbench now stages recaptures rather than accepting
an unvalidated locator update. Recapture must preserve the existing semantic
component type, framework, native class, and known application/window
ownership. The staged definition retains its UUID, authoring actions,
completion contract, expected states, visual profile and discovered options.

The proposed revision is validated through the repository's persisted schema
before being offered for review. When the operator accepts the proposal,
the workbench checks that the current object's revision still matches the
version that was captured. A changed object rejects the staged update.

Actual end-to-end locator resolution still depends on the installed
accessibility driver and its live environment; the current CI covers the
headless validation and repository contracts but **does not certify** GUI
highlighting or native recapture on RHEL 8.9.

## Embedded-to-canonical migration

An `.ahplan` with an `objects` map contains a real repository-shaped
snapshot, not merely an opaque cache. The plan's **Objects** menu now exposes:

1. **Inspect Embedded Repository**: inspect the embedded objects, UUIDs, types,
   owner UUIDs, and full stored locator definitions without altering them.
2. **Migrate Embedded Objects**: choose an existing `.ahobjects` destination,
   review each object against that repository, and explicitly resolve any
   name collision by mapping the source UUID to a compatible canonical UUID
   or renaming the incoming object.
3. **Export Portable Snapshot**: available after canonical migration; create
   a separate portable `.ahplan` using a copy of the current canonical
   object closure without adding embedded objects back into the canonical plan.

The migration process uses these rules:

- Identical UUID: retain the existing canonical object and its current name
  and definition. A stale embedded locator is not treated as a new version.
- UUID absent from canonical, name unique: import the object, preserving its
  UUID, behavior, and ownership (with parent UUIDs remapped as necessary).
- Name conflict with different UUID: fail closed until the operator explicitly
  maps the embedded identity to a compatible canonical object or renames
  the import. Matching names alone do **not** establish identity.
- Identical locator strategies with different UUIDs and names: report a
  **possible duplicate** and require an explicit mapping or distinct-import
  approval. Candidate matching is intentionally conservative, not a proof of
  logical equivalence; unmatched strategies do not establish distinctness.
- Same UUID with incompatible semantic object type: block automatic migration
  and require explicit identity review. Different snapshot revisions for the
  same UUID are displayed side-by-side; canonical definitions remain authoritative.
- Explicit UUID coalescence: preserve the chosen canonical UUID and combine
  locator alternatives only after the operator resolves the conflict.
- Step calls: rewrite recognized object reference fields to canonical UUIDs.
  The migrated plan contains `objects: {}` and `execution_mode: canonical`.

Publication stages the destination repository, plan, and owning Project
membership (when provided), using their original file fingerprints. It
does not delete existing recording repositories. If the plan already assigns
a **different** external repository, migration refuses to replace that
association: first reconcile those repositories at the Project level.

`canonical` is an external-only execution mode: the plan never silently
falls back to an embedded definition if a canonical UUID is absent. The
assigned repository is required at execution, including with pytest and the
CLI. Contrast with the existing `linked` mode, which can still fall back
to portable plan definitions, and `portable`/`locked` modes.

### Repository lifecycle and reverse references

When an Object Repository is opened from a Project, **Find Usages** lists
references from that Project's test plans and inline reusable steps. Deleting
an object with a known Project reference is blocked. Repository merge
operations reconcile affected Project plan and reusable-step references
before committing.

The reverse reference inventory is **Project-scoped**. It does not prove
that an object is unused by other Projects, external `.ahplan` files, or
dynamic/script-generated references. Standalone deletion explicitly warns
that external references have not been checked. Workspaces with shared
repositories across Projects need a workspace-wide dependency index for
stronger deletion safety.

## Compatibility and deliberate omissions

- Saved `.ahplan` files, including self-contained plans with embedded
  repository definitions, remain supported. No migration is automatic on open.
- Shared object repositories keep their existing UUID-based schema.
- Project-scoped repository merges now migrate references in the Project's
  persisted test plans and inline reusable steps in its Step Registries. Open
  the repository **through its owning Project** to use Merge / Deduplicate.
  Standalone merges are blocked because dependent artifact discovery would be
  incomplete. The Project membership is the transaction boundary; plans and
  registries outside that Project are not automatically discovered.
- A changed repository, plan, or Step Registry baseline aborts reconciliation
  without replacing a target. All affected artifact fingerprints are required
  for publication. The transaction stages files and restores earlier files on
  ordinary errors. It does **not** provide crash-atomic multi-file durability
  or a cross-process lock; unrelated writers must cooperate with these checks.
- When another Step Registry repository owns the source UUID, the GUI refuses
  the operation rather than leaving the other repository inconsistent.
- Runtime repository resolution supports linked, portable, locked, and canonical modes;
  linked uses the canonical current definition, portable uses the embedded
  snapshot, and locked requires matching snapshot provenance and content.
  canonical requires external definitions and has no embedded fallback.
  Plan saving preserves snapshot provenance where snapshots are intended,
  including nested read-only locator definitions transformed into ordinary
  portable YAML values.
- No historical branches are deleted or rewritten as part of this work.

## Validation

The Python 3.6 CI suite exercises
`test_repository_concurrency.py`, `test_recapture_safety.py`, and
`test_repository_preparation.py`, including cross-plan reconciliation,
legacy references, locked execution provenance, and stale-artifact rejection.
Passing CI does not replace qualification on the isolated RHEL desktop.
