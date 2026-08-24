# Cleanup 0.3.1

This cleanup removes ambiguous mocks/stubs identified in the 0.3.0 audit.

## Removed interaction bypass

- Deleted the synthetic `ui_activate` reference-protocol action.
- Deleted the reference-state UI activation implementation, including the `track.canvas` no-op path.
- Removed the legacy `reference` component strategy from production code and schema support.
- Activatable built-in components now use AT-SPI only.
- Added `reference_inspection` as a deliberately read-only strategy for synthetic metadata such as canvas bounds.
- Added explicit component actions; resolve-only components reject activation with `UnsupportedComponentAction`.

## Removed empty importable stubs

Twenty-six zero-byte implementation/config/test placeholders were removed from the package. Their intended names are retained in `PLANNED_SURFACE.md` instead of being distributed as apparently implemented modules.

## Qualification behavior

- The real AT-SPI UI regression has no fallback.
- If `pyatspi` is absent, the normal self-test records the AT-SPI integration as skipped.
- `automation-run selftest --require-atspi` fails qualification when `pyatspi` or the real AT-SPI path is unavailable.

## Verification

- Package zero-byte file scan: none.
- Production legacy `ui_activate` scan: none.
- Production legacy `type: reference` interaction strategy scan: none.
- Production synthetic activation `noop` scan: none.
- Framework tests: 19 passed.
- Fresh-wheel reference service/registry suite: 7 passed.
- Fresh-wheel reference UI suite on the build host: 1 passed, 1 skipped (`pyatspi` unavailable).
- Protected `ProtectedBackend` remains intentionally disabled and is not considered an accidental stub.
