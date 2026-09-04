"""Dedicated command-line entry point for self-contained TestPlan execution."""
from __future__ import annotations

import sys

from automation_harness.runner.cli import main


def run(argv=None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] in {"-h", "--help"}:
        arguments = ["plan", "run", *arguments]
    elif arguments[0] in {"run", "validate", "status"}:
        arguments = ["plan", *arguments]
    else:
        arguments = ["plan", "run", *arguments]
    return main(arguments)


if __name__ == "__main__":
    raise SystemExit(run())
