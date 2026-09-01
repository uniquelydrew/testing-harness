"""Python-version-safe entry points for live authoring/capture surfaces."""


def _prepare():
    from automation_harness.compat.python36 import install

    install()
    from automation_harness.authoring import app, live_runtime
    from automation_harness.authoring.live_click_policy import install as install_click_policy
    from automation_harness.authoring.targetless_runtime import install as install_targetless_runtime

    live_runtime.capture_runtime._install(app)
    live_runtime._install_workbench_controls()
    live_runtime._install_live_authoring(app)
    install_targetless_runtime(app)
    install_click_policy(app)
    return app


def run_author():
    app = _prepare()
    return app.main()


def run_capture():
    app = _prepare()
    return app.capture_main()


def run_repository():
    app = _prepare()
    return app.repository_main()
