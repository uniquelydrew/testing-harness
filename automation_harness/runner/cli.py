from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from automation_harness.backends.protected import ProtectedBackend
from automation_harness.backends.reference import ReferenceBackend
from automation_harness.backends.gtk_demo import GtkDemoBackend
from automation_harness.backends.java_desktop import JavaDesktopBackend
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.step_registry import default_step_registry
from automation_harness.core.test_plan import derive_execution_state, load_plan, validate_plan, validate_plan_components, validate_plan_execution
from automation_harness.runner.bundle import BundleError, TestBundle
from automation_harness.runner.execution import execute_bundle
from automation_harness.runner.plan_execution import execute_plan
from automation_harness.runner.validator import validate_bundle


def _backend(name: str, args: argparse.Namespace, target: dict | None = None):
    if name == "reference":
        return ReferenceBackend(
            gui=getattr(args, "reference_mode", "gui") == "gui",
            display_mode=getattr(args, "reference_display", "virtual"),
        )
    if name == "protected":
        return ProtectedBackend()
    if name == "gtk-demo":
        target = target or {}
        example = target.get("example") or getattr(args, "gtk_demo_example", None)
        if not isinstance(example, str) or not example:
            raise ValueError("GTK Demo backend requires bundle target.example")
        return GtkDemoBackend(
            example=example,
            executable=getattr(args, "gtk_demo_executable", None),
            display_mode=getattr(args, "gtk_demo_display", "virtual"),
        )
    if name == "java-desktop":
        target = target or {}
        if target.get("kind") != "java-desktop":
            raise ValueError("java-desktop backend requires manifest.target.kind: java-desktop")
        return JavaDesktopBackend(target, display_mode=getattr(args, "reference_display", "virtual"))
    raise ValueError(name)


def _add_reference_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--reference-mode",
        choices=("gui", "headless"),
        default="gui",
        help="synthetic reference target mode (default: gui)",
    )


def _add_gtk_demo_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--gtk-demo-executable", help="gtk4-demo executable (default: AUTOMATION_HARNESS_GTK_DEMO or gtk4-demo)")
    parser.add_argument("--gtk-demo-display", choices=("virtual", "native", "auto"), default="virtual")
    parser.add_argument(
        "--reference-display",
        choices=("virtual", "native", "auto"),
        default="virtual",
        help="GUI display policy; virtual uses an isolated Xvfb display (default)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="automation-run", description="Automation harness development runner")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="statically validate a test bundle without starting any target")
    validate.add_argument("bundle", type=Path)
    validate.add_argument("--backend", choices=("reference", "protected", "gtk-demo", "java-desktop"), default="reference")
    _add_reference_options(validate)
    _add_gtk_demo_options(validate)

    inspect = sub.add_parser("inspect", help="print normalized bundle metadata")
    inspect.add_argument("bundle", type=Path)

    steps = sub.add_parser("steps", help="inspect the reusable registered-step catalog")
    steps_sub = steps.add_subparsers(dest="steps_command", required=True)
    steps_list = steps_sub.add_parser("list", help="list registered reusable steps")
    steps_list.add_argument("--domain", help="filter by step domain")
    steps_list.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    steps_describe = steps_sub.add_parser("describe", help="describe one registered step")
    steps_describe.add_argument("name")
    steps_describe.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    plan = sub.add_parser("plan", help="inspect and validate declarative TestPlan files")
    plan_sub = plan.add_subparsers(dest="plan_command", required=True)
    plan_validate = plan_sub.add_parser("validate", help="validate a declarative TestPlan against the registered step catalog")
    plan_validate.add_argument("path", type=Path)
    plan_validate.add_argument("--backend", choices=("reference", "protected", "gtk-demo", "java-desktop"), help="also validate backend capabilities/risk policy")
    plan_validate.add_argument("--components", type=Path, help="additional object repository to overlay for validation")
    _add_reference_options(plan_validate)
    _add_gtk_demo_options(plan_validate)
    plan_status = plan_sub.add_parser("status", help="show the initial managed queue projection for a TestPlan")
    plan_status.add_argument("path", type=Path)
    plan_status.add_argument("--json", action="store_true")
    plan_run = plan_sub.add_parser("run", help="execute a declarative TestPlan using installed registered steps only")
    plan_run.add_argument("path", type=Path)
    plan_run.add_argument("--backend", choices=("reference", "protected", "gtk-demo", "java-desktop"), default="reference")
    plan_run.add_argument("--runs-dir", type=Path, default=Path("runs"))
    plan_run.add_argument("--var", dest="variables", action="append", default=[], metavar="NAME=VALUE")
    plan_run.add_argument("--components", type=Path, help="additional object repository to overlay for execution")
    _add_reference_options(plan_run)
    _add_gtk_demo_options(plan_run)

    selftest = sub.add_parser("selftest", help="run the built-in synthetic reference regression suites")
    selftest.add_argument("--runs-dir", type=Path, default=Path("runs"))
    selftest.add_argument(
        "--reference-display",
        choices=("virtual", "native", "auto"),
        default="virtual",
        help="GUI display policy for the built-in UI regression suite",
    )
    selftest.add_argument(
        "--require-atspi",
        action="store_true",
        help="fail qualification if the system pyatspi binding or real AT-SPI UI test is unavailable",
    )
    selftest.add_argument("-v", "--verbose", action="store_true")

    run = sub.add_parser("run", help="validate and execute a bundle")
    run.add_argument("bundle", type=Path)
    run.add_argument("--backend", choices=("reference", "protected", "gtk-demo", "java-desktop"), default="reference")
    run.add_argument("--runs-dir", type=Path, default=Path("runs"))
    run.add_argument("-v", "--verbose", action="store_true")
    run.add_argument(
        "--var",
        dest="variables",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="initialize/override a test-global variable; VALUE is parsed as JSON when possible",
    )
    _add_reference_options(run)
    _add_gtk_demo_options(run)

    gtk_demo = sub.add_parser("gtk-demo", help="run the version-pinned GTK 4.14 Demo baseline")
    gtk_demo_sub = gtk_demo.add_subparsers(dest="gtk_demo_command", required=True)
    gtk_selftest = gtk_demo_sub.add_parser("selftest", help="run all built-in GTK Demo bundles")
    gtk_selftest.add_argument("--runs-dir", type=Path, default=Path("runs"))
    gtk_selftest.add_argument("--gtk-demo-executable")
    gtk_selftest.add_argument("--gtk-demo-display", choices=("virtual", "native", "auto"), default="virtual")
    gtk_selftest.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "gtk-demo":
        examples = Path(__file__).resolve().parents[1] / "examples" / "gtk4_demo"
        suite_paths = sorted(path.parent for path in examples.glob("*/manifest.yaml"))
        results, exit_code = [], 0
        for suite_path in suite_paths:
            bundle = TestBundle.load(suite_path)
            backend = _backend("gtk-demo", args, bundle.target)
            result = execute_bundle(bundle, backend, runs_dir=args.runs_dir.resolve(), verbose=args.verbose)
            results.append(result.to_dict())
            exit_code = exit_code or int(result.exit_code or 0)
        print(json.dumps({"gtk_demo_selftest": results, "exit_code": exit_code}, indent=2, default=str))
        return exit_code

    if args.command == "plan":
        try:
            test_plan = load_plan(args.path)
        except Exception as exc:
            print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
        registry = default_step_registry()
        issues = validate_plan(test_plan, registry)
        package_components = Path(__file__).resolve().parents[1] / "resources" / "components.yaml"
        component_paths = [package_components]
        selected_components = getattr(args, "components", None)
        if selected_components is not None:
            component_paths.append(selected_components.resolve())
        component_repository = ComponentRepository.load(component_paths)
        issues.extend(validate_plan_components(test_plan, component_repository))
        if args.plan_command == "validate":
            if args.backend:
                backend = _backend(args.backend, args)
                issues.extend(
                    validate_plan_execution(
                        test_plan,
                        registry,
                        backend_capabilities=backend.capabilities,
                        allowed_step_risks=backend.allowed_step_risks,
                    )
                )
                issues.extend(f"backend preflight: {item}" for item in backend.preflight_issues())
            if issues:
                for issue in issues:
                    print(f"ERROR: {issue}", file=sys.stderr)
                return 2
            suffix = f" for backend={args.backend}" if args.backend else ""
            print(f"VALID: {test_plan.name} ({len(test_plan.steps)} step(s)){suffix}")
            return 0
        if args.plan_command == "run":
            try:
                variable_overrides = _parse_variable_overrides(args.variables)
            except ValueError as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                return 2
            backend = _backend(args.backend, args)
            result = execute_plan(
                test_plan,
                backend,
                runs_dir=args.runs_dir.resolve(),
                variable_overrides=variable_overrides,
                component_repository=(
                    ComponentRepository.load([selected_components.resolve()])
                    if selected_components is not None else None
                ),
            )
            print(json.dumps(result.to_dict(), indent=2, default=str))
            return int(result.exit_code or 0)
        state = derive_execution_state(test_plan)
        if args.json:
            print(json.dumps(state.to_dict(), indent=2, default=str))
        else:
            if issues:
                for issue in issues:
                    print(f"WARNING: {issue}", file=sys.stderr)
            for call in test_plan.steps:
                node = state.steps[call.node_id]
                waiting = ",".join(node.unresolved_variables) or "-"
                print(f"{call.node_id:<18} {call.step_id:<34} {node.status.value:<8} waiting={waiting}")
        return 2 if issues else 0

    if args.command == "steps":
        registry = default_step_registry()
        if args.steps_command == "list":
            definitions = registry.definitions(domain=args.domain)
            if args.json:
                print(json.dumps([definition.to_dict() for definition in definitions], indent=2))
            else:
                if args.domain and not definitions:
                    print(f"No registered steps in domain {args.domain!r}.")
                for definition in definitions:
                    capabilities = ",".join(sorted(definition.capabilities)) or "-"
                    print(f"{definition.name:<34} {str(definition.invocation_signature):<34} [{capabilities}] risk={definition.risk}")
            return 0
        try:
            definition = registry.get(args.name)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps(definition.to_dict(), indent=2))
        else:
            print(f"Name: {definition.name}")
            print(f"Domain: {definition.domain}")
            print(f"Signature: {definition.invocation_signature}")
            print(f"Capabilities: {', '.join(sorted(definition.capabilities)) or '-'}")
            print(f"Risk: {definition.risk}")
            print(f"Aliases: {', '.join(definition.aliases) or '-'}")
            print("Inputs:")
            if definition.inputs:
                for item in definition.inputs:
                    requirement = "required" if item.required else f"default={item.default!r}"
                    print(f"  - {item.name}: {item.annotation} ({item.kind}, {requirement})")
            else:
                print("  - none")
            print("Outputs:")
            if definition.outputs:
                for item in definition.outputs:
                    print(f"  - {item.name} <- {item.selector}")
            else:
                print("  - none")
            print(f"Source: {definition.source_module}:{definition.source_name}")
            print(f"Description: {definition.description or '-'}")
        return 0

    if args.command == "selftest":
        if args.require_atspi:
            try:
                import pyatspi  # type: ignore  # noqa: F401
            except ImportError:
                print(
                    "ERROR: --require-atspi requested but the system pyatspi binding is unavailable",
                    file=sys.stderr,
                )
                return 2
            if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
                if os.environ.get("AUTOMATION_HARNESS_ATSPI_SESSION"):
                    print(
                        "ERROR: --require-atspi needs a D-Bus session, but none was created",
                        file=sys.stderr,
                    )
                    return 2
                launcher = shutil.which("dbus-run-session")
                if launcher is None:
                    print(
                        "ERROR: --require-atspi needs a D-Bus session; install dbus-run-session or run inside a desktop session",
                        file=sys.stderr,
                    )
                    return 2
                environment = os.environ.copy()
                environment["AUTOMATION_HARNESS_ATSPI_SESSION"] = "1"
                return subprocess.run(
                    [launcher, "--", sys.executable, "-m", "automation_harness.runner.cli", *sys.argv[1:]],
                    env=environment,
                    check=False,
                ).returncode

        examples = Path(__file__).resolve().parents[1] / "examples"
        suite_paths = [examples / "reference_suite", examples / "reference_ui"]
        results = []
        exit_code = 0
        for suite_path in suite_paths:
            try:
                suite = TestBundle.load(suite_path)
            except BundleError as exc:
                print(f"ERROR: built-in selftest bundle is invalid: {exc}", file=sys.stderr)
                return 3
            backend = ReferenceBackend(gui=True, display_mode=args.reference_display)
            result = execute_bundle(
                suite,
                backend,
                runs_dir=args.runs_dir.resolve(),
                verbose=args.verbose,
            )
            results.append(result.to_dict())
            if result.exit_code and exit_code == 0:
                exit_code = int(result.exit_code)
            if args.require_atspi and suite_path.name == "reference_ui" and result.skipped:
                if exit_code == 0:
                    exit_code = 2
        print(json.dumps({"selftest": results, "exit_code": exit_code}, indent=2, default=str))
        return exit_code

    try:
        bundle = TestBundle.load(args.bundle)
    except BundleError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.command == "inspect":
        print(json.dumps({
            "name": bundle.name,
            "version": bundle.version,
            "requires": sorted(bundle.requires),
            "tests": [str(path.relative_to(bundle.root)) for path in bundle.tests],
            "components": str(bundle.components.relative_to(bundle.root)) if bundle.components else None,
            "step_libraries": [str(path.relative_to(bundle.root)) for path in bundle.step_libraries],
            "variables": bundle.variables or {},
            "target": bundle.target,
            "root": str(bundle.root),
        }, indent=2))
        return 0

    try:
        backend = _backend(args.backend, args, bundle.target)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.command == "validate":
        issues = validate_bundle(bundle, backend_capabilities=backend.capabilities)
        preflight = backend.preflight_issues()
        if issues or preflight:
            for issue in issues:
                print(f"ERROR: {issue}", file=sys.stderr)
            for issue in preflight:
                print(f"ERROR: backend preflight: {issue}", file=sys.stderr)
            return 2
        print(f"VALID: {bundle.name} ({len(bundle.tests)} test file(s)) for backend={backend.name}")
        return 0

    try:
        variable_overrides = _parse_variable_overrides(args.variables)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    result = execute_bundle(
        bundle,
        backend,
        runs_dir=args.runs_dir.resolve(),
        verbose=args.verbose,
        variable_overrides=variable_overrides,
    )
    print(json.dumps(result.to_dict(), indent=2, default=str))
    return int(result.exit_code or 0)


def _parse_variable_overrides(values: list[str]) -> dict[str, object]:
    result: dict[str, object] = {}
    for item in values:
        if "=" not in item:
            raise ValueError(f"--var requires NAME=VALUE, got {item!r}")
        name, raw = item.split("=", 1)
        name = name.strip()
        if not name or "." in name:
            raise ValueError(f"invalid --var name {name!r}; use a non-empty top-level name without '.'")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = raw
        result[name] = value
    return result


if __name__ == "__main__":
    raise SystemExit(main())
