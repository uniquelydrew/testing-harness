from __future__ import annotations

import os
import threading
from dataclasses import replace

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from automation_harness.authoring.gui.plan_authoring_window import TestPlanAuthoringWindow
from automation_harness.authoring.gui.preferences import recording_highlights_enabled
from automation_harness.authoring.plan_repository import (
    assigned_repository_path,
    ensure_default_repository,
    materialize_captured_target,
)
from automation_harness.authoring.project import AuthoringProject, save_authoring_project
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.test_plan import repository_from_plan
from automation_harness.drivers.javafx_bridge import HttpJavaFxBridgeTransport
from automation_harness.recording import RecordedInteraction, RecordingSession, RepositoryMatch, interactions_to_steps
from automation_harness.recording.adapters.atspi import AtspiRecordingAdapter
from automation_harness.recording.adapters.javafx import JavaFxRecordingAdapter
from automation_harness.recording.observations import PointerInteraction


class _ObservedRecordingAdapter:
    def __init__(self, delegate, observer):
        self.delegate = delegate
        self.observer = observer

    def start(self, emit):
        def observed(value):
            try:
                self.observer(value)
            finally:
                emit(value)
        return self.delegate.start(observed)

    def stop(self):
        return self.delegate.stop()


class RecordingTestPlanWindow(TestPlanAuthoringWindow):
    """Test Plan workflow with end-to-end desktop interaction recording."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.recording_session = None
        self.recording_stop_window = None
        self._recording_highlights = []
        self.start_recording_button = self.button("Start Recording", self.start_recording)
        self.stop_recording_button = self.button("Stop Recording", self.stop_recording)
        self.stop_recording_button.set_sensitive(False)
        self.window.show_all()

    def _recording_adapters(self):
        adapters = []
        highlight = recording_highlights_enabled()
        atspi = AtspiRecordingAdapter(
            on_resolved=self._recording_target_resolved if highlight else None,
        )
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
        javafx_adapters = [
            JavaFxRecordingAdapter(HttpJavaFxBridgeTransport(url.strip(), token.strip()))
            for url, token in zip(urls, tokens)
            if url.strip() and token.strip()
        ]
        if highlight:
            javafx_adapters = [_ObservedRecordingAdapter(item, self._recording_observation) for item in javafx_adapters]
        adapters.extend(javafx_adapters)
        return adapters

    def _recording_target_resolved(self, target, _acknowledgement_seconds=0.0):
        """Acknowledge semantic resolution while the physical button is still held."""
        if target is None or not target.bounds:
            return
        GLib.idle_add(self._show_recording_highlight, tuple(target.bounds))

    def _recording_observation(self, observation):
        # JavaFX agents may expose pointer-down directly. Highlight at press,
        # never at release: release is the commit boundary for the interaction.
        if not isinstance(observation, PointerInteraction):
            return
        if observation.phase != "pressed" or observation.target is None or not observation.target.bounds:
            return
        GLib.idle_add(self._show_recording_highlight, tuple(observation.target.bounds))

    def _show_recording_highlight(self, bounds):
        self._clear_recording_highlights()
        x, y, width, height = (int(value) for value in bounds)
        thickness = 4
        provider = Gtk.CssProvider(); provider.load_from_data(b"* { background-color: #ff3b30; }")
        rectangles = (
            (x, y, width, thickness),
            (x, y + max(0, height - thickness), width, thickness),
            (x, y, thickness, height),
            (x + max(0, width - thickness), y, thickness, height),
        )
        for rx, ry, rw, rh in rectangles:
            edge = Gtk.Window(type=Gtk.WindowType.POPUP)
            edge.set_decorated(False); edge.set_keep_above(True); edge.set_accept_focus(False)
            edge.set_opacity(0.88); edge.move(rx, ry); edge.resize(max(1, rw), max(1, rh))
            edge.get_style_context().add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            edge.show_all(); self._recording_highlights.append(edge)
        GLib.timeout_add(550, self._clear_recording_highlights)
        return False

    def _clear_recording_highlights(self):
        for window in tuple(self._recording_highlights):
            try: window.destroy()
            except Exception: pass
        self._recording_highlights = []
        return False

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
        self.stop_recording_button.set_sensitive(False); self._clear_recording_highlights()
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

        resolved = []; unresolved = []; captured_count = 0
        assigned_path = assigned_repository_path(self.plan, self.path)
        assigned_repository = ComponentRepository.load((assigned_path,)) if assigned_path and assigned_path.exists() else None

        for interaction in interactions or ():
            reviewed = interaction
            if interaction.repository_match.component_id is None and interaction.target is not None and interaction.repository_match.status in {"new_candidate", "unresolved"}:
                try:
                    if assigned_repository is None:
                        self.plan, assigned_path = ensure_default_repository(self.plan, self.path)
                        assigned_repository = ComponentRepository.load((assigned_path,))
                        self.assigned_repository_path = assigned_path
                        if self.project_context:
                            project = AuthoringProject.load(self.project_context).with_object_repository(assigned_path)
                            save_authoring_project(self.project_context, project); self.project = project
                    assigned_repository, component_id, created = materialize_captured_target(assigned_repository, interaction.target)
                    reviewed = replace(interaction, repository_match=RepositoryMatch("known_unique", (component_id,)))
                    if created: captured_count += 1
                except Exception:
                    unresolved.append(interaction); continue
            elif interaction.repository_match.component_id is None:
                unresolved.append(interaction); continue

            try:
                call = interactions_to_steps((reviewed,), start_index=len(self.plan.steps) + len(resolved) + 1)[0]
                resolved.append(replace(call, group="Recorded session"))
            except Exception:
                unresolved.append(interaction)

        if assigned_repository is not None and assigned_path is not None:
            assigned_repository.save(assigned_path)
            self.repository = repository_from_plan(self.plan).overlay(assigned_repository)
            if self.registry_resources:
                self.repository = self.repository.overlay(self.registry_resources.repository)

        if resolved or captured_count:
            if resolved:
                self.plan = replace(self.plan, steps=(*self.plan.steps, *resolved))
            self.mark_dirty(); self.refresh_all()
        self.set_status("Recording complete: %d actions added, %d new objects captured, %d unresolved" % (len(resolved), captured_count, len(unresolved)))
        if unresolved:
            self.info(
                "Recording Review",
                "%d interaction(s) remain ambiguous or lack a semantic target. Use repository deduplication/OR merge when multiple existing objects represent the same target." % len(unresolved),
            )
        return False
