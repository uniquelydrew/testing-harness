from __future__ import annotations

import os
import threading
from dataclasses import replace

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from automation_harness.authoring.gui.plan_authoring_window import TestPlanAuthoringWindow
from automation_harness.drivers.javafx_bridge import HttpJavaFxBridgeTransport
from automation_harness.recording import RecordingSession, interactions_to_steps
from automation_harness.recording.adapters.atspi import AtspiRecordingAdapter
from automation_harness.recording.adapters.javafx import JavaFxRecordingAdapter


class RecordingTestPlanWindow(TestPlanAuthoringWindow):
    """Test Plan workflow with end-to-end desktop interaction recording."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.recording_session = None
        self.recording_stop_window = None
        self.start_recording_button = self.button("Start Recording", self.start_recording)
        self.stop_recording_button = self.button("Stop Recording", self.stop_recording)
        self.stop_recording_button.set_sensitive(False)
        self.window.show_all()

    def _recording_adapters(self):
        adapters = []
        atspi = AtspiRecordingAdapter()
        if atspi.available:
            adapters.append(atspi)
        urls = os.environ.get(
            "AUTOMATION_HARNESS_JAVAFX_AGENT_URLS",
            os.environ.get("AUTOMATION_HARNESS_JAVAFX_AGENT_URL", ""),
        ).split(",")
        tokens = os.environ.get(
            "AUTOMATION_HARNESS_JAVAFX_AGENT_TOKENS",
            os.environ.get("AUTOMATION_HARNESS_JAVAFX_AGENT_TOKEN", ""),
        ).split(",")
        adapters.extend(
            JavaFxRecordingAdapter(HttpJavaFxBridgeTransport(url.strip(), token.strip()))
            for url, token in zip(urls, tokens)
            if url.strip() and token.strip()
        )
        return adapters

    def start_recording(self):
        if self.recording_session is not None:
            return
        adapters = self._recording_adapters()
        if not adapters:
            return self.error("Recording", "No AT-SPI desktop session or configured JavaFX recording agent is available.")
        session = RecordingSession(adapters, repository=self.repository)
        try:
            session.start()
        except Exception as exc:
            return self.error("Recording", "%s: %s" % (type(exc).__name__, exc))
        self.recording_session = session
        self.start_recording_button.set_sensitive(False); self.stop_recording_button.set_sensitive(True)
        self._show_recording_stop_window()
        self.set_status("Recording — hold targets until semantic resolution completes, then release")

    def _show_recording_stop_window(self):
        self.window.hide()
        stop = Gtk.Window(type=Gtk.WindowType.TOPLEVEL); stop.set_title("Automation Harness Recording"); stop.set_keep_above(True); stop.set_decorated(False); stop.set_border_width(10)
        button = Gtk.Button(label="Stop Recording"); button.set_size_request(190, 54); button.connect("clicked", lambda *_args: self.stop_recording())
        stop.add(button); stop.connect("delete-event", lambda *_args: (self.stop_recording(), True)[1]); stop.set_position(Gtk.WindowPosition.CENTER); stop.show_all()
        self.recording_stop_window = stop

    def stop_recording(self):
        if self.recording_session is None:
            return
        session = self.recording_session; self.recording_session = None
        self.stop_recording_button.set_sensitive(False)
        if self.recording_stop_window is not None:
            self.recording_stop_window.destroy(); self.recording_stop_window = None
        self.window.show_all(); self.window.present(); self.set_status("Stopping recording…")

        def worker():
            try: interactions = tuple(session.stop())
            except Exception as exc: GLib.idle_add(self._recording_finished, None, exc); return
            GLib.idle_add(self._recording_finished, interactions, None)
        threading.Thread(target=worker, name="automation-plan-recording-stop", daemon=True).start()

    def _recording_finished(self, interactions, error):
        self.start_recording_button.set_sensitive(True)
        if error is not None:
            self.set_status("Recording failed"); self.error("Recording", "%s: %s" % (type(error).__name__, error)); return False
        resolved = []; unresolved = []
        for interaction in interactions or ():
            if interaction.repository_match.component_id is None:
                unresolved.append(interaction); continue
            try:
                call = interactions_to_steps((interaction,), start_index=len(self.plan.steps) + len(resolved) + 1)[0]
                resolved.append(replace(call, group="Recorded session"))
            except Exception:
                unresolved.append(interaction)
        if resolved:
            self.plan = replace(self.plan, steps=(*self.plan.steps, *resolved)); self.mark_dirty(); self.refresh_all()
        self.set_status("Recording complete: %d added, %d unresolved" % (len(resolved), len(unresolved)))
        if unresolved:
            self.info(
                "Recording Review",
                "%d interaction(s) could not be bound to known repository objects. Open the Object Repository to capture/reconcile those targets before adding them to the Test Plan." % len(unresolved),
            )
        return False
