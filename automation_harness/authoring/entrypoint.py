"""Canonical application entry point."""
from __future__ import annotations


def main(argv=None):
    # Xlib threading must be initialized before GTK imports create a display.
    from automation_harness.recording.x11_threads import initialize_x11_threads
    initialize_x11_threads()

    # The modular GUI owns authoring behavior directly; do not install runtime
    # patches over the retired monolithic AuthoringApp.
    from automation_harness.authoring.gui.launcher import main as launch
    return launch(argv)
