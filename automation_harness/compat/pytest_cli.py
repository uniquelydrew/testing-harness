"""Entry point for the optional pytest plan adapter."""

import sys


def main(argv=None):
    import pytest

    arguments = list(sys.argv[1:] if argv is None else argv)
    return pytest.main([
        "-p", "automation_harness.integrations.pytest.plugin",
    ] + arguments)


if __name__ == "__main__":
    raise SystemExit(main())
