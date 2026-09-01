from __future__ import annotations

"""CLI policy for live desktop execution and external step implementations."""

import sys
from pathlib import Path

from automation_harness.backends.live_desktop import LiveDesktopBackend
from automation_harness.core.script_steps import load_script_steps


def _find_subparser(parser, name):
    import argparse

    if parser is None:
        return None
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices.get(name)
    return None


def _remove_option(parser, option):
    if parser is None:
        return
    for action in list(parser._actions):
        if option not in getattr(action, "option_strings", ()):
            continue
        parser._remove_action(action)
        for value in action.option_strings:
            parser._option_string_actions.pop(value, None)


def _install_cli_policy(cli_module):
    if getattr(cli_module, "_live_environment_policy_installed", False):
        return
    original_build = cli_module.build_parser
    original_backend = cli_module._backend

    def build_parser():
        parser = original_build()
        plan = _find_subparser(parser, "plan")
        plan_validate = _find_subparser(plan, "validate")
        plan_run = _find_subparser(plan, "run")
        validate = _find_subparser(parser, "validate")
        bundle_run = _find_subparser(parser, "run")

        # Tests are not scoped to one application. Object identity/ownership is
        # resolved from the object repository at the point of interaction.
        for target in (validate, plan_validate, plan_run, bundle_run):
            _remove_option(target, "--application")

        # The catalog used for validation must be the same catalog used for
        # execution. External implementations are therefore accepted by both
        # validation and run commands rather than being a run-only concern.
        for target in (validate, plan_validate, plan_run, bundle_run):
            if target is not None:
                target.add_argument(
                    "--script-step",
                    action="append",
                    default=[],
                    type=Path,
                    help=(
                        "load a contract-backed script step manifest; repeat for "
                        "multiple external step implementations"
                    ),
                )
        return parser

    def backend(name, args, target=None):
        if name == "attached-desktop":
            return LiveDesktopBackend()
        return original_backend(name, args, target)

    cli_module.build_parser = build_parser
    cli_module._backend = backend
    cli_module._live_environment_policy_installed = True


def _extract_script_steps(argv):
    paths: list[Path] = []
    for index, value in enumerate(argv):
        if value == "--script-step":
            if index + 1 >= len(argv):
                raise ValueError("--script-step requires a manifest path")
            paths.append(Path(argv[index + 1]).expanduser().resolve())
        elif value.startswith("--script-step="):
            paths.append(Path(value.split("=", 1)[1]).expanduser().resolve())
    return paths


def main(argv=None):
    from automation_harness.runner import cli

    _install_cli_policy(cli)
    values = list(sys.argv[1:] if argv is None else argv)
    try:
        load_script_steps(_extract_script_steps(values))
    except Exception as exc:
        print("ERROR: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 2
    return cli.main(values)
