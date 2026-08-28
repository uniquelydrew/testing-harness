from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib


def _install_capture_next_click(app_module):
    """Route GTK Capture Next Click through the AT-SPI global event listener.

    The initial GTK migration used a nearly-transparent fullscreen Gtk.Window
    to intercept the next click.  On GNOME Classic/X11 that popup can be shown
    without receiving the pointer release event, leaving the UI to time out
    before the ObjectCaptureService is ever called.  The AT-SPI driver already
    implements the correct X11 global-click/focus listener, so use that as the
    authoritative path instead of duplicating click interception in GTK.
    """

    def capture_next_click(self):
        if not self.capture.available:
            return self._error("AT-SPI unavailable", "pyatspi is not installed on this host.")
        if self._click_capture_active:
            return
        self._click_capture_active = True
        self._set_status("Waiting for the next desktop click via AT-SPI…")
        self.window.hide()

        def worker():
            try:
                captured = self.capture.capture_next_click(timeout=30.0)
            except Exception as exc:
                GLib.idle_add(self._finish_next_click_capture, None, exc)
            else:
                GLib.idle_add(self._finish_next_click_capture, captured, None)

        threading.Thread(
            target=worker,
            name="automation-atspi-click-capture",
            daemon=True,
        ).start()

    app_module.AuthoringApp.capture_next_click = capture_next_click


def run_author(argv=None):
    from automation_harness.authoring import app

    _install_capture_next_click(app)
    return app.main(argv)


def run_capture(argv=None):
    from automation_harness.authoring import app

    _install_capture_next_click(app)
    return app.capture_main(argv)
