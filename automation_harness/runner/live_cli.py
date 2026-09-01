from __future__ import annotations

"""CLI extension for contract-backed external step implementations."""

import sys
from pathlib import Path

from automation_harness.core.script_steps import load_script_steps


def _find_subparser(parser, name):
    import argparse

    if parser is None:
        return None
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices.get(name)
    return None


def _install_cli_policy(cli_module):
    if getattr(cli_module, "_script_step_cli_installed", False):
        return
    original_build = cli_module.build_parser

    def build_parser():
        parser = original_build()
        plan = _find_subparser(parser, "plan")
        plan_validate = _find_subparser(plan, "validate")
        plan_run = _find_subparser(plan, "run")
        validate = _find_subparser(parser, "validate")
        bundle_run = _find_subparser(parser, "run")

        # The catalog used for validation must be the same catalog used for
        # execution, so external implementations are accepted by both paths.
        for command in (validate, plan_validate, plan_run, bundle_run):
            if command is not None:
                command.add_argument(
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

    cli_module.build_parser = build_parser
    cli_module._script_step_cli_installed = True


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
