"""Python-version-safe entry point for live desktop CLI execution."""


def run_cli():
    from automation_harness.compat.python36 import install

    install()
    from automation_harness.runner.live_cli import main

    return main()
