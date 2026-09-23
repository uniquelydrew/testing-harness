from __future__ import annotations

import threading
from dataclasses import replace

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from automation_harness.authoring.gui.plan_authoring_window import TestPlanAuthoringWindow
from automation_harness.authoring.gui.preferences import recording_highlights_enabled
from automation_harness.authoring.preferences_runtime import AuthoringPreferences
from automation_harness.authoring.recording_review import (
    materialize_recorded_interaction,
)
from automation_harness.authoring.plan_repository import (
    assigned_repository_path,
    ensure_default_repository,
)
from automation_harness.authoring.project import AuthoringProject, save_authoring_project
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.logical_menu import (
    LogicalMenuTarget,
    logical_menu_metadata,
    logical_menu_target_is_persisted,
)
from automation_harness.core.test_plan import repository_from_plan
from automation_harness.drivers.java_agent import configured_java_recording_transports
from automation_harness.recording import RecordingSession, interactions_to_steps
from automation_harness.recording.adapters.atspi import AtspiRecordingAdapter
from automation_harness.recording.adapters.javafx import JavaFxRecordingAdapter
from automation_harness.recording.observations import PointerInteraction
from automation_harness.recording.diagnostics import RecordingDebugLog


_ACTIVE_RECORDING_WINDOW = None
_ACTIVE_RECORDING_LOCK = threading.RLock()


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

    def set_diagnostic_sink(self, sink):
        setter = getattr(self.delegate, "set_diagnostic_sink", None)
        if callable(setter):
            setter(sink)


class RecordingTestPlanWindow(TestPlanAuthoringWindow):
    """Test Plan workflow with end-to-end desktop interaction recording."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.recording_session = None
        self.recording_stop_window = None
        self._recording_stop_pending = False
        self._recording_highlights = []
        self._recording_diagnostic_session = None
        self.recording_toggle_button = self.button("Start Recording", self.toggle_recording)
        self.recording_toggle_button.set_tooltip_text(
            "Start or stop the single active recording session"
        )
        self.window.connect("destroy", lambda *_args: self._release_recording_owner())
        self.window.show_all()

    def _recording_adapters(self):
        adapters = []
        highlight = recording_highlights_enabled()
        atspi = AtspiRecordingAdapter(
            on_resolved=self._recording_target_resolved if highlight else None,
        )
        if atspi.available:
            adapters.append(atspi)
        javafx_adapters = [
            JavaFxRecordingAdapter(transport)
            for transport in configured_java_recording_transports()
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

    def _set_recording_toggle_state(self, *, active=False, stopping=False):
        if stopping:
            self.recording_toggle_button.set_label("Stopping…")
            self.recording_toggle_button.set_sensitive(False)
        else:
            self.recording_toggle_button.set_label("Stop Recording" if active else "Start Recording")
            self.recording_toggle_button.set_sensitive(True)

    def _release_recording_owner(self):
        global _ACTIVE_RECORDING_WINDOW
        with _ACTIVE_RECORDING_LOCK:
            # A destroyed window cannot release a session that is still active;
            # retaining the owner prevents another window from recording over it.
            if _ACTIVE_RECORDING_WINDOW is self and self.recording_session is None:
                _ACTIVE_RECORDING_WINDOW = None

    def toggle_recording(self):
        if self.recording_session is not None:
            return self.stop_recording()
        return self.start_recording()

    def start_recording(self):
        global _ACTIVE_RECORDING_WINDOW
        if self.recording_session is not None or self._recording_stop_pending:
            return
        adapters = self._recording_adapters()
        if not adapters:
            return self.error("Recording", "No AT-SPI desktop session or configured JavaFX recording agent is available.")
        preferences = AuthoringPreferences.load()
        debug_log = None
        if preferences.recording_verbose_debug:
            debug_log = RecordingDebugLog(
                preferences.resolved_runs_dir(getattr(self, "project", None)) / "recording-debug"
            )
        recording_repository = self.repository
        existing_path = assigned_repository_path(self.plan, self.path)
        if existing_path is not None and existing_path.exists():
            recording_repository = recording_repository.overlay(
                ComponentRepository.load((existing_path,))
            )
        if self.registry_resources:
            recording_repository = recording_repository.overlay(self.registry_resources.repository)
        session = RecordingSession(
            adapters, repository=recording_repository,
            diagnostics=bool(debug_log), debug_log=debug_log,
        )
        try:
            with _ACTIVE_RECORDING_LOCK:
                if _ACTIVE_RECORDING_WINDOW is not None and _ACTIVE_RECORDING_WINDOW is not self:
                    return self.info(
                        "Recording",
                        "Another Test Plan is already recording. Stop that recording before starting a new one.",
                    )
                session.start()
                self.recording_session = session
                _ACTIVE_RECORDING_WINDOW = self
        except Exception as exc:
            return self.error("Recording", "%s: %s" % (type(exc).__name__, exc))
        self._set_recording_toggle_state(active=True)
        self._show_recording_stop_window()
        self.set_status("Recording — hold targets until semantic resolution completes, then release")

    def _show_recording_stop_window(self):
        self.window.hide()
        stop = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        stop.set_title("Automation Harness Recording")
        stop.set_keep_above(True)
        stop.set_decorated(False)
        stop.set_border_width(8)
        stop.set_resizable(False)
        stop.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)

        # The overlay is intentionally undecorated so it does not compete with
        # the application under test. Give it an explicit drag handle instead
        # of forcing the user to sacrifice screen real estate or hunt for a
        # window-manager border.
        surface = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        surface.set_border_width(4)
        handle = Gtk.EventBox()
        handle.set_visible_window(False)
        handle.set_tooltip_text("Drag to move the recording control")
        handle_label = Gtk.Label(label="● Recording")
        handle_label.set_xalign(0.0)
        handle.add(handle_label)
        handle.connect(
            "button-press-event",
            lambda _widget, event: (
                stop.begin_move_drag(1, int(event.x_root), int(event.y_root), event.time),
                True,
            )[1] if event.button == 1 else False,
        )
        surface.pack_start(handle, True, True, 0)

        button = Gtk.Button(label="Stop Recording")
        button.set_size_request(190, 54)
        button.set_tooltip_text("Stop recording and return to the Test Plan")
        button.connect("clicked", lambda *_args: self.toggle_recording())
        surface.pack_start(button, False, False, 0)
        stop.add(surface)
        stop.connect("delete-event", lambda *_args: (self.stop_recording(), True)[1])
        stop.set_position(Gtk.WindowPosition.CENTER)
        stop.show_all()
        self.recording_stop_window = stop

    def stop_recording(self):
        global _ACTIVE_RECORDING_WINDOW
        if self.recording_session is None or self._recording_stop_pending:
            return
        with _ACTIVE_RECORDING_LOCK:
            session = self.recording_session
            self.recording_session = None
            self._recording_diagnostic_session = session
            self._recording_stop_pending = True
            # Retain the global owner until session.stop() completes so another
            # Test Plan cannot begin recording during adapter shutdown.
        self._set_recording_toggle_state(stopping=True)
        self._clear_recording_highlights()
        if self.recording_stop_window is not None:
            self.recording_stop_window.destroy(); self.recording_stop_window = None
        self.window.show_all(); self.window.present(); self.set_status("Stopping recording…")

        def worker():
            try: interactions = tuple(session.stop())
            except Exception as exc: GLib.idle_add(self._recording_finished, None, exc); return
            GLib.idle_add(self._recording_finished, interactions, None)
        threading.Thread(target=worker, name="automation-plan-recording-stop", daemon=True).start()

    def _ensure_review_repository(self, repository, path):
        """Create/load the assigned repository only when review needs to mutate it."""
        if repository is not None:
            return repository, path
        self.plan, path = ensure_default_repository(self.plan, self.path)
        repository = ComponentRepository.load((path,))
        self.assigned_repository_path = path
        if self.project_context:
            project = AuthoringProject.load(self.project_context).with_object_repository(path)
            save_authoring_project(self.project_context, project)
            self.project = project
        return repository, path

    @staticmethod
    def _menu_target_for_review(interaction):
        match = interaction.repository_match
        if (
            match.status == "known_subobject"
            and match.component_id is not None
            and match.subobject_path
        ):
            return LogicalMenuTarget(match.component_id, match.subobject_path, ())
        return None

    @classmethod
    def _review_requires_persistent_repository(cls, interaction, recording_repository):
        """Predict whether shared review will create or update repository data."""
        if interaction.target is None:
            return False
        evidence = dict(interaction.evidence or {})
        if evidence.get("menu_invoking_capture") is not None:
            return True
        match = interaction.repository_match
        if match.component_id is None and match.status in {"new_candidate", "unresolved"}:
            return True
        target = cls._menu_target_for_review(interaction)
        if evidence.get("menu_owner_capture") is not None:
            return not (
                target is not None
                and recording_repository is not None
                and logical_menu_target_is_persisted(recording_repository, target)
            )
        if logical_menu_metadata(interaction.target) is not None:
            return not (
                target is not None
                and recording_repository is not None
                and logical_menu_target_is_persisted(recording_repository, target)
            )
        return False

    def _recording_finished(self, interactions, error):
        global _ACTIVE_RECORDING_WINDOW
        with _ACTIVE_RECORDING_LOCK:
            self._recording_stop_pending = False
            if _ACTIVE_RECORDING_WINDOW is self:
                _ACTIVE_RECORDING_WINDOW = None
        self._set_recording_toggle_state(active=False)
        diagnostic_session = self._recording_diagnostic_session
        diagnostic_path = getattr(diagnostic_session, "diagnostic_path", None)
        if error is not None:
            if diagnostic_session is not None:
                diagnostic_session.diagnostic_exception("recording_finish_failed", error)
            self.set_status("Recording failed")
            self.error("Recording", "%s: %s" % (type(error).__name__, error))
            return False

        resolved = []
        unresolved = []
        captured_ids = set()
        provisional_ids = set()
        assigned_path = assigned_repository_path(self.plan, self.path)
        assigned_repository = (
            ComponentRepository.load((assigned_path,))
            if assigned_path and assigned_path.exists()
            else None
        )
        recording_repository = getattr(diagnostic_session, "repository", None)

        for interaction in interactions or ():
            if diagnostic_session is not None:
                diagnostic_session.diagnostic(
                    "review_interaction_started", interaction=interaction,
                )
            if assigned_repository is None and self._review_requires_persistent_repository(
                interaction, recording_repository,
            ):
                assigned_repository, assigned_path = self._ensure_review_repository(
                    assigned_repository, assigned_path,
                )

            review_repository = assigned_repository or recording_repository or self.repository
            if review_repository is None:
                review_repository = ComponentRepository({})
            outcome = materialize_recorded_interaction(
                review_repository,
                interaction,
                recording_repository=recording_repository,
            )

            if outcome.error is not None:
                provisional_ids.update(outcome.provisional_component_ids)
                captured_ids.update(outcome.created_component_ids)
                # A provisional object is intentionally retained for review;
                # shared materialization returns that candidate while refusing
                # to produce an executable step. Other failures are rolled back.
                if outcome.provisional_component_ids and assigned_repository is not None:
                    assigned_repository = outcome.repository
                if diagnostic_session is not None:
                    diagnostic_session.diagnostic_exception(
                        "review_interaction_failed",
                        outcome.error,
                        interaction=interaction,
                        provisional_component_ids=outcome.provisional_component_ids,
                    )
                unresolved.append(interaction)
                continue

            if assigned_repository is not None:
                assigned_repository = outcome.repository
            captured_ids.update(outcome.created_component_ids)
            reviewed = outcome.interaction
            if reviewed.repository_match.component_id is None:
                unresolved.append(interaction)
                if diagnostic_session is not None:
                    diagnostic_session.diagnostic(
                        "review_interaction_unresolved",
                        interaction=interaction,
                        reason="shared_review_returned_no_unique_component",
                    )
                continue

            try:
                call = interactions_to_steps(
                    (reviewed,),
                    start_index=len(self.plan.steps) + len(resolved) + 1,
                )[0]
                resolved.append(replace(call, group="Recorded session"))
                if diagnostic_session is not None:
                    diagnostic_session.diagnostic(
                        "review_step_created", interaction=reviewed,
                        step=resolved[-1],
                        changed_component_ids=outcome.changed_component_ids,
                        inventory_changed=outcome.inventory_changed,
                    )
            except Exception as exc:
                if diagnostic_session is not None:
                    diagnostic_session.diagnostic_exception(
                        "review_step_conversion_failed", exc,
                        interaction=reviewed,
                    )
                unresolved.append(interaction)

        if assigned_repository is not None and assigned_path is not None:
            assigned_repository.save(assigned_path)
            self.repository = repository_from_plan(self.plan).overlay(assigned_repository)
            if self.registry_resources:
                self.repository = self.repository.overlay(self.registry_resources.repository)

        if resolved or captured_ids:
            if resolved:
                self.plan = replace(self.plan, steps=(*self.plan.steps, *resolved))
            self.mark_dirty()
            self.refresh_all()
        self.set_status(
            "Recording complete: %d actions added, %d new objects captured, %d provisional, %d unresolved"
            % (len(resolved), len(captured_ids), len(provisional_ids), len(unresolved))
        )
        if diagnostic_session is not None:
            diagnostic_session.diagnostic(
                "recording_review_complete",
                resolved_steps=resolved,
                unresolved_interactions=unresolved,
                captured_count=len(captured_ids),
                provisional_count=len(provisional_ids),
                assigned_path=assigned_path,
                final_repository=assigned_repository.to_document() if assigned_repository else None,
                plan=self.plan,
            )
        if unresolved:
            log_note = "\n\nVerbose diagnostic log: %s" % diagnostic_path if diagnostic_path else ""
            self.info(
                "Recording Review",
                "%d interaction(s) remain unresolved or provisional. Provisional objects were retained in the Object Repository for review but were not added as executable Test Plan steps. Use repository deduplication/OR merge when multiple existing objects represent the same target.%s"
                % (len(unresolved), log_note),
            )
        elif diagnostic_path:
            self.set_status(
                "Recording complete: %d actions added — diagnostics: %s"
                % (len(resolved), diagnostic_path)
            )
        self._recording_diagnostic_session = None
        return False
