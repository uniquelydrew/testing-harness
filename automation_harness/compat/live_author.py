"""Python-version-safe entry points for authoring/capture surfaces."""


def _initialize_display_threads():
    # Must run before importing GTK/GDK or any other Xlib consumer.  Recording
    # owns a background Xlib display for physical press timing, while GTK uses
    # X11 on the main thread.
    from automation_harness.recording.x11_threads import initialize_x11_threads

    initialize_x11_threads()


def _install_click_capture_policy():
    from automation_harness.authoring.atspi_click_capture_policy import install

    install()


def _prepare_legacy():
    from automation_harness.compat.python36 import install

    install()
    _initialize_display_threads()
    _install_click_capture_policy()
    from automation_harness.authoring import (
        app,
        live_runtime,
        registry_portability_runtime,
        registry_runtime,
        standalone_registry_runtime,
    )
    from automation_harness.authoring.live_click_policy import install as install_click_policy
    from automation_harness.authoring.preferences_runtime import install as install_preferences_runtime
    from automation_harness.formats import STEP_REGISTRY_SUFFIX

    live_runtime.capture_runtime._install(app)
    live_runtime._install_workbench_controls()
    install_click_policy(app)
    install_preferences_runtime(app)
    app.STEP_REGISTRY_SUFFIX = STEP_REGISTRY_SUFFIX
    registry_runtime.install(app)
    registry_portability_runtime.install(app)
    standalone_registry_runtime.install(app)
    return app


def run_author():
    from automation_harness.compat.python36 import install

    install()
    _initialize_display_threads()
    _install_click_capture_policy()
    from automation_harness.authoring.gui.launcher import main

    return main()


def run_capture():
    app = _prepare_legacy()
    return app.capture_main()


def run_repository():
    app = _prepare_legacy()
    return app.repository_main()
