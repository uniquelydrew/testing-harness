# 0.5.1 Namespace Cleanup

- Removed the environment-specific legacy identifier from the application namespace, package metadata, CLI commands, backend names, GUI titles, internal environment variables, test metadata, documentation, and examples.
- Renamed the Python package to `automation_harness` and the distribution to `automation-harness`.
- Renamed CLI entry points to `automation-run`, `automation-reference`, and `automation-author`.
- Renamed the disabled environment adapter to `ProtectedBackend` with backend ID `protected`.
- Renamed runtime environment variables to the `AUTOMATION_HARNESS_*` namespace.
- Preserved the reference/protected execution boundary and all 0.5.0 behavior.
