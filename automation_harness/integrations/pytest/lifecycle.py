"""Explicit, bounded process lifecycle around declarative plan runs.

No shell is spawned: callers must name the executable and its arguments.
Unlike a later managed plan step, teardown runs even after step execution fails.
"""
from __future__ import annotations

import shlex
import subprocess
from pathlib import Path


class LifecycleError(RuntimeError):
    """A configured setup/teardown command failed or could not be started."""


def run_command(command: str, *, phase: str, cwd: Path, timeout: int):
    try:
        args = shlex.split(command)
    except ValueError as exc:
        raise LifecycleError("%s command syntax: %s" % (phase, exc)) from exc
    if not args:
        raise LifecycleError("%s command must not be empty" % phase)
    try:
        completed = subprocess.run(
            args, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LifecycleError("%s command failed: %s: %s" % (
            phase, type(exc).__name__, exc,
        )) from exc
    if completed.returncode:
        raise LifecycleError(
            "%s command exited %s: %s\nstdout: %s\nstderr: %s" % (
                phase, completed.returncode, command,
                completed.stdout[-1500:], completed.stderr[-1500:],
            )
        )
    return completed
