from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib

from automation_harness.core.hybrid_object_capture import HybridObjectCaptureService


def _install_capture_backend(app_module):
    """Install the RHEL desktop capture service before AuthoringApp starts.

    Native GTK/Swing applications remain on AT-SPI. Instrumented JavaFX JVMs
    are captured through the JavaFX scene-graph bridge because Linux OpenJFX
    does not expose those Nodes through AT-SPI.
    """
    app_module.ObjectCaptureService = HybridObjectCaptureService


def _install_capture_next_click(app_module):
    """Capture the next desktop click without intercepting the pointer in GTK."""

    def capture_next_click(self):
        if not self.capture.available:
            return self._error(
                "Capture unavailable",
                "Neither AT-SPI nor an instrumented JavaFX bridge is available on this host.",
            )
        if self._click_capture_active:
            return
        self._click_capture_active = True
        self._set_status("Waiting for the next desktop click…")
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
            name="automation-desktop-click-capture",
            daemon=True,
        ).start()

    app_module.AuthoringApp.capture_next_click = capture_next_click


def _install_javafx_authoring(app_module):
    """Keep the existing GTK editor while making JavaFX identity first-class."""

    def present_capture(self, captured):
        self._last_capture = captured
        if hasattr(self, "highlight_button"):
            self.highlight_button.set_sensitive(bool(captured.bounds))
        assessments = [item.to_dict() for item in self.capture.assess(captured)]
        self._set_text(
            self.object_detail,
            json.dumps(
                {"capture": captured.to_dict(), "locator_assessments": assessments},
                indent=2,
                default=str,
            ),
        )
        if self.repository_path is None:
            if not self._confirm("Save capture", "No editable repository is open. Choose a repository file now?"):
                return
            path = self._choose_file(save=True, yaml=True)
            if not path:
                return
            self.repository_path = Path(path)
        component_id = self._ask_text("Save capture", "Logical component ID:")
        if not component_id:
            return
        try:
            strategy = captured.candidate_strategy()
            if strategy.type == "anchored_visual":
                definition = self.capture.save_capture(self.repository_path, component_id, captured)
            else:
                if strategy.type == "javafx":
                    candidate = strategy.options.get("identification")
                    prompt = "JavaFX identity JSON:"
                else:
                    candidate = captured.candidate_identification().to_dict()
                    prompt = "AT-SPI identity JSON:"
                if not isinstance(candidate, dict):
                    raise ValueError("captured object has no editable identity mapping")
                identity_raw = self._ask_text(
                    "Object identification",
                    prompt,
                    json.dumps(candidate, separators=(",", ":")),
                    multiline=True,
                )
                if identity_raw is None:
                    return
                identification = json.loads(identity_raw)
                if not isinstance(identification, dict):
                    raise ValueError("identification must be a JSON object")
                definition = self.capture.save_capture(
                    self.repository_path,
                    component_id,
                    captured,
                    identification=identification,
                )
        except Exception as exc:
            return self._error("Object identification", "%s: %s" % (type(exc).__name__, exc))
        self.repository = self._load_repository()
        self.refresh_objects()
        self._set_status("Saved %s revision %s" % (definition.component_id, definition.revision))
        if captured.bounds and self._confirm("Visual capture", "Stage a component-bounds visual candidate now?"):
            try:
                result = self.capture.stage_visual_capture(self.repository_path, component_id, captured)
                self._set_status("Staged visual candidate: " + result["variant_key"])
                self._info("Visual candidate", json.dumps(result, indent=2, default=str))
            except Exception as exc:
                self._error("Visual capture", "%s: %s" % (type(exc).__name__, exc))

    def resolve_selected_for_highlight(self, definition):
        deadline = time.monotonic() + 5.0
        last_error = None
        while time.monotonic() < deadline:
            for strategy in definition.strategies:
                try:
                    if strategy.type == "anchored_visual":
                        captured = self.capture.resolve_anchored_visual(strategy.options)
                    elif strategy.type in {"atspi", "java_accessibility"}:
                        captured = self.capture.capture_by_locator(
                            identification=strategy.options.get("identification")
                        )
                    elif strategy.type == "javafx":
                        captured = self.capture.javafx_driver.inspect(
                            identification=strategy.options.get("identification")
                        )
                    else:
                        continue
                    GLib.idle_add(self._finish_repository_highlight, captured, None)
                    return
                except Exception as exc:
                    last_error = exc
            time.sleep(0.2)
        GLib.idle_add(
            self._finish_repository_highlight,
            None,
            LookupError(
                "No live object matched %r within 5 seconds. %s"
                % (definition.component_id, last_error or "")
            ),
        )

    app_module.AuthoringApp._present_capture = present_capture
    app_module.AuthoringApp._resolve_selected_for_highlight = resolve_selected_for_highlight


def _install(app_module):
    _install_capture_backend(app_module)
    _install_capture_next_click(app_module)
    _install_javafx_authoring(app_module)


def run_author(argv=None):
    from automation_harness.authoring import app

    _install(app)
    return app.main(argv)


def run_capture(argv=None):
    from automation_harness.authoring import app

    _install(app)
    return app.capture_main(argv)


def run_repository(argv=None):
    from automation_harness.authoring import app

    _install(app)
    return app.repository_main(argv)
