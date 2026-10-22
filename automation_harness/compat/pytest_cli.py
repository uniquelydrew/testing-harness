"""Python 3.6-safe entry point for the optional pytest plan adapter."""

import sys


def main(argv=None):
    from automation_harness.compat.python36 import install

    install()
    import pytest

    arguments = list(sys.argv[1:] if argv is None else argv)
    # The harness Python 3.6 compatibility loader transforms source at import time.
    # Pytest assertion rewriting bypasses that loader and attempts to compile
    # Python 3.7+ syntax directly (e.g. postponed annotations).
    return pytest.main([
        "--assert=plain",
        "-p", "automation_harness.integrations.pytest.plugin",
    ] + arguments)


if __name__ == "__main__":
    raise SystemExit(main())
