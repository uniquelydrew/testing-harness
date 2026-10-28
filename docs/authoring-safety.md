# Repository authoring safety

This document covers the focused authoring safeguards on the
`codex/repository-authoring-safety-remediation` branch, developed against
current `main`. It does not activate or migrate older experimental
reconciliation behavior.

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

## Compatibility and deliberate omissions

- Saved `.ahplan` files, including self-contained plans with embedded
  repository definitions, remain supported. No migration is automatic on open.
- Shared object repositories keep their existing UUID-based schema.
- Legacy plan-object migration and coordinated plan/repository transactions
  from `codex/authoring-repository-reconciliation-followups` are **not**
  imported. They need independent design and validation against current
  plan ownership and the pytest adapter.
- No historical branches are deleted or rewritten as part of this work.

## Validation

The Python 3.6 CI suite exercises
`test_repository_concurrency.py` and `test_recapture_safety.py`.
Passing CI does not replace qualification on the isolated RHEL desktop.
