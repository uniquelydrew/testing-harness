#!/usr/bin/env python3
"""Adapt the private copy of the legacy RHEL pyatspi sources to Python 3.12.

The distribution-owned files are never modified. Only the two known Python
3.7+ reserved-keyword constructs in upstream pyatspi 2.26's registry.py are
rewritten, and all copied Python modules are compiled before they are used.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import shutil


_LEGACY_ASSIGNMENT = re.compile(
    r"(?m)^(?P<indent>[ \t]*)self\.async[ \t]*=[ \t]*False(?P<comment>[ \t]*(?:#.*)?)$"
)
_LEGACY_START = re.compile(
    r"(?m)^(?P<indent>[ \t]*)def start\(self,[ \t]*async=False,[ \t]*gil=True\):[ \t]*$"
)


def adapt_registry(source: str) -> tuple[str, bool]:
    """Rewrite only the known legacy syntax, preserving the public API."""
    assignments = list(_LEGACY_ASSIGNMENT.finditer(source))
    signatures = list(_LEGACY_START.finditer(source))
    if not assignments and not signatures:
        # Already modern syntax: do not guess at transformations.
        compile(source, "registry.py", "exec")
        return source, False
    if len(assignments) != 1 or len(signatures) != 1:
        raise ValueError(
            "Unsupported legacy pyatspi registry layout: expected exactly one "
            "self.async assignment and one start(async=...) declaration"
        )

    # setattr preserves the old dynamic attribute name for getattr users.
    result = _LEGACY_ASSIGNMENT.sub(
        lambda match: (
            match.group("indent")
            + 'setattr(self, "async", False)'
            + match.group("comment")
        ),
        source,
        count=1,
    )

    # Preserve both original positional arguments and the legacy async keyword
    # for clients using start(**{"async": True}). This mirrors upstream's fix.
    result = _LEGACY_START.sub(
        lambda match: (
            match.group("indent")
            + "def start(self, asynchronous=False, gil=True, **kwargs):\n"
            + match.group("indent")
            + '    if "async" in kwargs:\n'
            + match.group("indent")
            + '        asynchronous = kwargs.pop("async")\n'
            + match.group("indent")
            + "    if kwargs:\n"
            + match.group("indent")
            + '        raise TypeError("Unexpected pyatspi Registry.start keyword(s): " '
            + '+ ", ".join(sorted(kwargs)))'
        ),
        result,
        count=1,
    )
    compile(result, "registry.py", "exec")
    return result, True


def install_compatible_copy(source_dir: Path, target_dir: Path) -> bool:
    """Copy system sources and preflight their Python syntax in isolation."""
    src = source_dir.resolve()
    dest = target_dir.resolve()
    if src == dest or src in dest.parents:
        raise ValueError("Refusing to modify distribution-owned pyatspi sources")
    if not (src / "__init__.py").is_file() or not (src / "registry.py").is_file():
        raise FileNotFoundError(f"Missing pyatspi Python sources in {src}")

    # Copy to a staging directory first: a syntax failure must not leave an
    # invalid or partially adapted installation in the virtual environment.
    import tempfile

    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".pyatspi-stage-", dir=dest.parent) as temp:
        staging = Path(temp) / "pyatspi"
        shutil.copytree(
            src,
            staging,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
        registry = staging / "registry.py"
        patched, changed = adapt_registry(registry.read_text(encoding="utf-8"))
        if changed:
            registry.write_text(patched, encoding="utf-8")

        for module in sorted(staging.rglob("*.py")):
            compile(module.read_bytes(), str(module), "exec")

        # Replacing an existing staged copy is safe because the RPM is separate.
        # Retain a backup until the new tree has been promoted successfully.
        backup = Path(temp) / "old-pyatspi"
        if dest.exists():
            if not dest.is_dir() or dest.is_symlink():
                raise ValueError(f"Refusing to replace non-directory: {dest}")
            dest.rename(backup)
        try:
            staging.rename(dest)
        except Exception:
            if backup.exists():
                backup.rename(dest)
            raise
        return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("target_dir", type=Path)
    args = parser.parse_args()
    try:
        changed = install_compatible_copy(args.source_dir, args.target_dir)
    except Exception as exc:
        parser.exit(1, f"[bootstrap] pyatspi compatibility error: {exc}\n")
    if changed:
        print("[bootstrap] Applied Python 3.12 reserved-keyword compatibility fix to the private pyatspi copy")
    else:
        print("[bootstrap] pyatspi sources already use Python 3.12-compatible syntax")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
