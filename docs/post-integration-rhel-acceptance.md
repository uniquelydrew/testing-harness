# Post-integration RHEL/GTK acceptance (PR #39)

**Scope:** Development/test VM only. Do not modify production or protected
environment configuration. Use disposable copies of plans and object repositories,
not the canonical ERSA artifacts.

Automated Python/Java CI cannot certify native GTK/AT-SPI event-loop teardown,
foreground application focus, or modal migration approval. This checklist is the
remaining in-person acceptance contract; attach evidence to PR #39. A checked
item is **not** proof until a tester actually records its outcome.

## Preconditions and evidence

- Check out `codex/post-integration-remediation` (record `git rev-parse HEAD`).
- Use the existing qualified RHEL 8.9/X11 desktop environment and existing
  harness virtualenv. Do not rerun bootstrap or change protected configuration
  merely to perform this validation.
- Record `DISPLAY`, `XDG_SESSION_TYPE`, Python interpreter origin,
  installed `pyatspi` version if exposed, and commit hash in the evidence notes.
- Start the authoring GUI through the existing `automation-codex-runtime launch`
  wrapper if that is the usual harness launch path. Use
  `automation-codex-runtime report` and `snapshot` to preserve diagnostics.
- Keep timestamped test notes/screenshots and an exit/exception summary; remove
  or redact sensitive plan contents before sharing.

## AT-SPI event-loop acceptance

1. With a nonproduction accessible AUT, start recording from the plan authoring
   GUI. Capture a native button click and an editable text field change.
2. Stop recording. Verify it completes without hangs, SIGSEGV, GTK fatal errors,
   or a lingering `Stopping recording...` state. Text events must resolve to
   their actual editable source, not the prior mouse target.
3. Without restarting the authoring process, repeat start → capture → stop at
   least three times, including one transient menu selection and one text change.
   Verify one resolved observation per intended action and no stale registration.
4. Run the deterministic teardown tests on the same host (within the existing
   environment):

   ```bash
   python -m pytest -q automation_harness/tests/test_atspi_recording_adapter.py -k 'timeout or callback or text'
   ```

   This validates the simulated callback timeout, late completion, lease release
   and subsequent new session. It does **not** intentionally stall native
   callbacks in the production AUT.
5. If a real timeout occurs, preserve the first exception and the
   `atspi_stop_callback_drain_timeout`, `atspi_stop_lease_retained`,
   `atspi_deferred_lease_released` lifecycle events. Retry recording only
   after the old native callbacks have completed, not while still active.

**Pass condition:** No process instability, repeated recording works with
correct semantics, and a delayed callback neither releases the registry early
nor permanently strands a subsequent session.

## Legacy migration approval/cancellation

Use **disposable** copies of a plan with embedded objects and two repositories:
a validated existing repository and an intentionally missing assigned path.
Keep initial SHA-256 hashes of all copies.

1. Open a linked legacy plan whose configured repository path is missing.
   The editor should open without composing the missing external repository.
   Normal Save/Validate/Run and Add Object Action remain blocked until migration.
2. Select **Objects → Migrate Embedded Objects**, then choose the existing
   replacement repository copy. Verify a separate warning names the missing
   previous assignment and the proposed replacement.
3. Cancel that warning. Verify the original plan and repository checksums and
   original assignment remain exactly unchanged.
4. Repeat and approve the missing-path recovery warning, then cancel in the
   per-object UUID/conflict-review dialog. Verify both artifacts unchanged.
5. Repeat, review each collision explicitly, then cancel the final publication
   confirmation. Verify both artifacts unchanged.
6. Repeat and approve all stages. Verify the canonical plan has
   `execution_mode: canonical`, has no embedded `objects`, and references the
   selected repository with the same approved UUID remappings. Verify the
   destination repository contains the approved import, and that the plan can
   be saved, reloaded, validated and run under appropriate test conditions.
7. Separately, attempt migration to a different repository while the original
   assigned repository **still exists**. The operation must reject the switch
   without modifying either artifact.
8. In a concurrent-change test, make the previously missing repository path
   appear during the modal review. The second pre-publication eligibility check
   must reject publication; no destination files may change.
9. Open an intentionally portable plan with embedded objects. Its portable
   behavior must remain available without forced canonical migration.

**Pass condition:** All cancellation paths are nonmutating; no existing valid
repository is silently replaced; missing-path recovery requires explicit
approval, per-object review, and guarded atomic publication.

## Semantic matching and visual projection

- Check JavaFX MenuButton/Menu, ChoiceBox/ComboBox and Swing JMenu/JMenuBar/
  JMenuItem/JComboBox/JToggleButton as applicable in the nonproduction AUT.
- Check AT-SPI editable text and check-menu-item roles. Visual highlighting
  must fail visibly and safely if the target has semantic identity but lacks
  usable bounds. It must not draw at (0,0) or borrow another object's geometry.
- Preserve failed resolution evidence for mismatched menu/item, button/toggle,
  text/label and combo/menu types. These must remain fail-closed.

## Exit criteria

Attach the completed run checklist, commit SHA, runtime report/snapshot path,
relevant event log and test results to PR #39. Do not mark live RHEL validation
complete from green headless CI alone. Keep PR in draft until reviewed acceptance
evidence is attached.
