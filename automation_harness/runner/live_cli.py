from __future__ import annotations

"""CLI environment-startup wrapper for live desktop execution."""

import os
import subprocess
import sys
from pathlib import Path

from automation_harness.backends.live_desktop import LiveDesktopBackend


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

        # A live-desktop run is scoped by object locators, not a single test-
        # level application name. Remove the obsolete selector everywhere.
        for target in (validate, plan_validate, plan_run, bundle_run):
            _remove_option(target, "--application")

        for target in (plan_run, bundle_run):
            if target is not None:
                target.add_argument(
                    "--environment-script",
                    type=Path,
                    help=(
                        "launch the environment before standalone execution; "
                        "authoring runs assume the environment is already up"
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


def _extract_environment_script(argv):
    for index, value in enumerate(argv):
        if value == "--environment-script":
            if index + 1 >= len(argv):
                raise ValueError("--environment-script requires a path")
            return Path(argv[index + 1]).expanduser().resolve()
        if value.startswith("--environment-script="):
            return Path(value.split("=", 1)[1]).expanduser().resolve()
    return None


def _is_execution_command(argv):
    if not argv:
        return False
    if argv[0] == "run":
        return True
    return len(argv) >= 2 and argv[0] == "plan" and argv[1] == "run"


def _start_environment(script):
    if not script.is_file():
        raise FileNotFoundError("environment startup script does not exist: %s" % script)
    result = subprocess.run(
        [str(script)],
        cwd=str(script.parent),
        env=os.environ.copy(),
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            "environment startup script failed with exit code %d: %s"
            % (result.returncode, script)
        )


def main(argv=None):
    from automation_harness.runner import cli

    _install_cli_policy(cli)
    values = list(sys.argv[1:] if argv is None else argv)
    try:
        startup = _extract_environment_script(values)
        if startup is not None and _is_execution_command(values):
            _start_environment(startup)
    except Exception as exc:
        print("ERROR: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 2
    return cli.main(values)
