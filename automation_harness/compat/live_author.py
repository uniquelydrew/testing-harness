"""Python-version-safe entry point for the live authoring console."""


def run_author():
    from automation_harness.compat.python36 import install

    install()
    from automation_harness.authoring.live_runtime import run_author as _run_author

    return _run_author()
