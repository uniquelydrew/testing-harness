"""RHEL/Python-3.6 GTK Object Capture behavior overrides.

This module intentionally uses Python 3.6 syntax because modules under
``automation_harness.compat`` bypass the source compatibility transformer.
"""

import threading


def install(module):
    """Patch AuthoringApp to use AT-SPI global click capture directly."""
    AuthoringApp = module.AuthoringApp
    GLib = module.GLib

    def capture_next_click(self):
        if not self.capture.available:
            return self._error("AT-SPI unavailable", "pyatspi is not installed on this host.")
        if self._click_capture_active:
            return
        self._click_capture_active = True
        self._set_status("Waiting for next AT-SPI desktop click (30 seconds)...")
        self.window.hide()

        def worker():
            try:
                captured = self.capture.capture_next_click(timeout=30.0)
            except Exception as exc:
                GLib.idle_add(self._finish_next_click_capture, None, exc)
            else:
                GLib.idle_add(self._finish_next_click_capture, captured, None)

        thread = threading.Thread(
            target=worker,
            name="automation-atspi-click-capture",
        )
        thread.daemon = True
        thread.start()

    AuthoringApp.capture_next_click = capture_next_click
