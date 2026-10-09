"""Canonical application entry point."""
from __future__ import annotations


def _install_thread_dump_signal():
    try:
        import faulthandler
        import signal
        import sys
        if hasattr(signal, "SIGUSR1"):
            faulthandler.register(signal.SIGUSR1, file=sys.stderr, all_threads=True)
    except (AttributeError, OSError, RuntimeError, ValueError):
        pass


def main(argv=None):
    _install_thread_dump_signal()
    # Xlib threading must be initialized before GTK imports create a display.
    from automation_harness.recording.x11_threads import initialize_x11_threads
    initialize_x11_threads()

    # The modular GUI owns authoring behavior directly; do not install runtime
    # patches over the retired monolithic AuthoringApp.
    from automation_harness.authoring.gui.launcher import main as launch
    return launch(argv)


if __name__ == "__main__":
    raise SystemExit(main())
