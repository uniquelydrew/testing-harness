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
from automation_harness.backends.live_desktop import LiveDesktopBackend
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.visual_baselines import VisualProfile, approve_visual_candidate, reject_visual_candidate, stage_visual_candidate
from automation_harness.core.step_registry import default_step_registry
from automation_harness.core.test_plan import derive_execution_state, load_plan, validate_plan, validate_plan_components, validate_plan_execution
from automation_harness.runner.bundle import BundleError, TestBundle
from automation_harness.runner.execution import execute_bundle
from automation_harness.runner.plan_execution import execute_plan
from automation_harness.runner.validator import validate_bundle


_BACKEND_CHOICES = ("reference", "protected", "gtk-demo", "java-desktop", "live-desktop")


def _backend(name: str, args: argparse.Namespace, backend_config: dict | None = None):
    if name == "reference":
        return ReferenceBackend(
            gui=getattr(args, "reference_mode", "gui") == "gui",
            display_mode=getattr(args, "reference_display", "virtual"),
        )
    if name == "protected":
        return ProtectedBackend()
    if name == "live-desktop":
        return LiveDesktopBackend()
    if name == "gtk-demo":
        config = backend_config or {}
        example = config.get("example") or getattr(args, "gtk_demo_example", None)
        if not isinstance(example, str) or not example:
            raise ValueError("GTK Demo backend requires backend.example")
        return GtkDemoBackend(
            example=example,
            executable=getattr(args, "gtk_demo_executable", None),
            display_mode=getattr(args, "gtk_demo_display", "virtual"),
        )
    if name == "java-desktop":
        config = backend_config or {}
        if config.get("kind") != "java-desktop":
            raise ValueError("java-desktop backend requires manifest.backend.kind: java-desktop")
        return JavaDesktopBackend(config, display_mode=getattr(args, "reference_display", "virtual"))
    raise ValueError(name)


def _add_reference_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--reference-mode",
        choices=("gui", "headless"),
        default="gui",
        help="synthetic reference backend mode (default: gui)",
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

    validate = sub.add_parser("validate", help="statically validate a test bundle without starting a backend")
    validate.add_argument("bundle", type=Path)
    validate.add_argument("--backend", choices=_BACKEND_CHOICES)
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

    visual = sub.add_parser("visual", help="stage and review component-bound visual baselines")
    visual_sub = visual.add_subparsers(dest="visual_command", required=True)
    visual_stage = visual_sub.add_parser("stage", help="capture a candidate from component bounds")
    visual_stage.add_argument("repository", type=Path)
    visual_stage.add_argument("component_id")
    visual_stage.add_argument("--bounds", required=True, metavar="X,Y,WIDTH,HEIGHT")
    visual_stage.add_argument("--profile", action="append", default=[], metavar="KEY=VALUE")
    visual_stage.add_argument("--pixel-tolerance", type=int, default=12)
    visual_stage.add_argument("--max-difference-ratio", type=float, default=0.01)
    visual_approve = visual_sub.add_parser("approve", help="promote a staged candidate")
    visual_approve.add_argument("repository", type=Path)
    visual_approve.add_argument("component_id")
    visual_approve.add_argument("variant_key")
    visual_approve.add_argument("--mask", type=Path)
    visual_reject = visual_sub.add_parser("reject", help="discard a staged candidate")
    visual_reject.add_argument("repository", type=Path)
    visual_reject.add_argument("component_id")
    visual_reject.add_argument("variant_key")
    visual_status = visual_sub.add_parser("status", help="show approved visual variants")
    visual_status.add_argument("repository", type=Path)
    visual_status.add_argument("component_id")

    plan = sub.add_parser("plan", help="inspect and validate declarative TestPlan files")
    plan_sub = plan.add_subparsers(dest="plan_command", required=True)
    plan_validate = plan_sub.add_parser("validate", help="validate a declarative TestPlan against the registered step catalog")
    plan_validate.add_argument("path", type=Path)
    plan_validate.add_argument("--backend", choices=_BACKEND_CHOICES, help="also validate backend capabilities/risk policy")
    plan_validate.add_argument("--components", type=Path, help="additional object repository to overlay for validation")
    _add_reference_options(plan_validate)
    _add_gtk_demo_options(plan_validate)
    plan_status = plan_sub.add_parser("status", help="show the initial managed queue projection for a TestPlan")
    plan_status.add_argument("path", type=Path)
    plan_status.add_argument("--json", action="store_true")
    plan_run = plan_sub.add_parser("run", help="execute a declarative TestPlan using installed registered steps only")
    plan_run.add_argument("path", type=Path)
    plan_run.add_argument("--backend", choices=_BACKEND_CHOICES, default="live-desktop")
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
    run.add_argument("--backend", choices=_BACKEND_CHOICES)
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

    # Python 3.6's argparse compatibility path cannot enforce required
    # subparsers. Do not fall through and assume bundle-specific arguments.
    if getattr(args, "command", None) is None:
        build_parser().print_help(sys.stderr)
        return 2

    if args.command == "visual":
        try:
            repository_path = args.repository.resolve()
            if args.visual_command == "stage":
                bounds = tuple(int(part.strip()) for part in args.bounds.split(","))
                if len(bounds) != 4:
                    raise ValueError("--bounds requires X,Y,WIDTH,HEIGHT")
                definition = ComponentRepository.load([repository_path]).get(args.component_id)
                result = stage_visual_candidate(
                    repository_path, definition, bounds, profile=VisualProfile.current(_parse_profile_overrides(args.profile)),
                    pixel_tolerance=args.pixel_tolerance, max_difference_ratio=args.max_difference_ratio,
                )
                print(json.dumps(result, indent=2, default=str))
            elif args.visual_command == "approve":
                result = approve_visual_candidate(repository_path, args.component_id, args.variant_key, mask=args.mask)
                print(json.dumps({"component_id": result.component_id, "visual": result.visual}, indent=2))
            elif args.visual_command == "reject":
                reject_visual_candidate(repository_path, args.component_id, args.variant_key)
                print(f"REJECTED: {args.component_id} {args.variant_key}")
            else:
                definition = ComponentRepository.load([repository_path]).get(args.component_id)
                print(json.dumps(definition.visual or {"variants": {}}, indent=2))
            return 0
        except Exception as exc:
            print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2

    if args.command == "gtk-demo":
        examples = Path(__file__).resolve().parents[1] / "examples" / "gtk4_demo"
        suite_paths = sorted(path.parent for path in examples.glob("*/manifest.yaml"))
        results, exit_code = [], 0
        for suite_path in suite_paths:
            bundle = TestBundle.load(suite_path)
            backend = _backend("gtk-demo", args, bundle.backend)
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
                try:
                    backend = _backend(args.backend, args)
                except ValueError as exc:
                    print(f"ERROR: {exc}", file=sys.stderr)
                    return 2
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
            try:
                backend = _backend(args.backend, args)
            except ValueError as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                return 2
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
            "backend": bundle.backend,
            "root": str(bundle.root),
        }, indent=2))
        return 0

    selected_backend = args.backend or ((bundle.backend or {}).get("kind")) or "reference"
    try:
        backend = _backend(selected_backend, args, bundle.backend)
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


def _parse_profile_overrides(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in values:
        if "=" not in item:
            raise ValueError(f"--profile requires KEY=VALUE, got {item!r}")
        key, value = item.split("=", 1)
        if not key or not value:
            raise ValueError(f"--profile requires non-empty KEY=VALUE, got {item!r}")
        result[key] = value
    return result


if __name__ == "__main__":
    raise SystemExit(main())
