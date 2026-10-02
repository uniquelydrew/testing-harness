from __future__ import annotations

import faulthandler
import os
import threading
import time
from dataclasses import replace
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from automation_harness.authoring.gui.plan_authoring_window import TestPlanAuthoringWindow
from automation_harness.authoring.gui.preferences import recording_highlights_enabled
from automation_harness.authoring.preferences_runtime import AuthoringPreferences
from automation_harness.authoring.recording_review import (
    materialize_recorded_interaction,
)
from automation_harness.authoring.plan_repository import (
    assigned_repository_path,
    ensure_default_repository,
    recording_repository_path,
    materialize_captured_target,
)
from automation_harness.authoring.project import AuthoringProject, save_authoring_project
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.logical_menu import (
    LogicalMenuTarget,
    logical_menu_metadata,
    logical_menu_target_is_persisted,
)
from automation_harness.core.test_plan import embed_plan_repository, repository_from_plan
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
        self._recording_stop_pending = False
        self._recording_generation = 0
        self._recording_completion_source = None
        self._recording_completion_read_fd = None
        self._recording_highlights = []
        self._recording_highlight_timeout = None
        self._recording_highlight_generation = 0
        self._recording_diagnostic_session = None
        self.recording_toggle_button = self.button("Start Recording", self.toggle_recording)
        self.recording_toggle_button.set_tooltip_text(
            "Start or stop the single active recording session"
        )
        self.window.connect("delete-event", self._recording_delete_event)
        self.window.connect("destroy", lambda *_args: self._release_recording_owner())
        self.window.connect("destroy", lambda *_args: self._cancel_recording_completion_watch())
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
        if self.recording_session is not None and not self._recording_stop_pending:
            GLib.idle_add(self._show_recording_highlight, tuple(target.bounds))

    def _recording_observation(self, observation):
        # JavaFX agents may expose pointer-down directly. Highlight at press,
        # never at release: release is the commit boundary for the interaction.
        if not isinstance(observation, PointerInteraction):
            return
        if observation.phase != "pressed" or observation.target is None or not observation.target.bounds:
            return
        if self.recording_session is not None and not self._recording_stop_pending:
            GLib.idle_add(self._show_recording_highlight, tuple(observation.target.bounds))

    def _show_recording_highlight(self, bounds):
        # Queued callbacks can outlive the adapter or arrive after Stop.
        if self.recording_session is None or self._recording_stop_pending:
            return False
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
        generation = self._recording_highlight_generation
        self._recording_highlight_timeout = GLib.timeout_add(
            550, self._expire_recording_highlight, generation,
        )
        return False

    def _expire_recording_highlight(self, generation):
        if generation == self._recording_highlight_generation:
            self._recording_highlight_timeout = None
            self._clear_recording_highlights()
        return False

    def _clear_recording_highlights(self):
        self._recording_highlight_generation += 1
        timeout = self._recording_highlight_timeout
        self._recording_highlight_timeout = None
        if timeout is not None:
            GLib.source_remove(timeout)
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

    def _recording_delete_event(self, *_args):
        """Do not destroy GTK surfaces while native recording teardown is active."""
        if self.recording_session is not None:
            self.stop_recording()
            self.set_status("Stopping recording before closing…")
            return True
        if self._recording_stop_pending:
            self.set_status("Waiting for recording shutdown before closing…")
            return True
        return False

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
        runtime_session = os.environ.get("AUTOMATION_HARNESS_CODEX_SESSION_DIR")
        if runtime_session:
            debug_log = RecordingDebugLog(Path(runtime_session) / "recording-debug")
        elif preferences.recording_verbose_debug:
            debug_log = RecordingDebugLog(
                preferences.resolved_runs_dir(getattr(self, "project", None)) / "recording-debug"
            )
        recording_repository = self.repository
        existing_path = recording_repository_path(self.plan, self.path)
        assigned_path = assigned_repository_path(self.plan, self.path)
        if (
            existing_path is not None
            and existing_path.exists()
            and (assigned_path is None or existing_path.resolve() != assigned_path.resolve())
        ):
            # self.repository is already refreshed from an assigned repository.
            # Only compose a distinct recording repository; reloading and
            # overlaying the assigned file onto itself adds duplicate identity
            # work at the beginning of every subsequent recording generation.
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
        self._recording_generation += 1
        self._set_recording_toggle_state(active=True)
        # Keep recording control inside the persistent Test Plan toplevel. A
        # second native GTK toplevel previously crossed recording generations
        # and was implicated in repeat-cycle SIGSEGVs.
        self.set_status("Recording — hold targets until semantic resolution completes, then release")

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
        # Keep GTK work on the main loop and native adapter shutdown on the
        # worker. Completion is signalled through a one-shot pipe watch below.
        self.set_status("Stopping recording…")
        GLib.idle_add(self._prepare_recording_stop_ui)

        completion = {}
        generation = self._recording_generation
        read_fd, write_fd = os.pipe()
        os.set_blocking(read_fd, False)
        self._recording_completion_read_fd = read_fd
        self._recording_completion_source = GLib.io_add_watch(
            read_fd,
            GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR,
            self._recording_stop_completion_ready,
            generation,
            completion,
        )
        session.diagnostic(
            "recording_stop_completion_watch_armed",
            generation=generation,
            source_id=self._recording_completion_source,
        )

        def worker():
            watchdog_handle = None
            watchdog_path = None
            try:
                diagnostic_root = os.environ.get("AUTOMATION_HARNESS_DIAGNOSTIC_DIR")
                if diagnostic_root:
                    Path(diagnostic_root).mkdir(parents=True, exist_ok=True)
                    watchdog_path = Path(diagnostic_root) / (
                        "recording-stop-hang-%d-%d.log" % (os.getpid(), int(time.time()))
                    )
                    watchdog_handle = open(str(watchdog_path), "w")
                    faulthandler.dump_traceback_later(
                        7.0, repeat=False, file=watchdog_handle,
                    )
                    session.diagnostic(
                        "recording_stop_watchdog_armed",
                        path=str(watchdog_path),
                        timeout_seconds=7.0,
                    )
                completion["interactions"] = tuple(session.stop())
                completion["error"] = None
            except Exception as exc:
                completion["interactions"] = None
                completion["error"] = exc
            finally:
                if watchdog_handle is not None:
                    try:
                        faulthandler.cancel_dump_traceback_later()
                    finally:
                        watchdog_handle.flush()
                        watchdog_handle.close()
                session.diagnostic(
                    "recording_stop_worker_completed",
                    generation=generation,
                    error=str(completion.get("error")) if completion.get("error") is not None else None,
                )
                try:
                    os.write(write_fd, b"1")
                except OSError:
                    pass
                finally:
                    try:
                        os.close(write_fd)
                    except OSError:
                        pass
        threading.Thread(target=worker, name="automation-plan-recording-stop", daemon=True).start()

    def _cancel_recording_completion_watch(self):
        source_id = self._recording_completion_source
        self._recording_completion_source = None
        if source_id is not None:
            try:
                GLib.source_remove(source_id)
            except Exception:
                pass
        read_fd = self._recording_completion_read_fd
        self._recording_completion_read_fd = None
        if read_fd is not None:
            try:
                os.close(read_fd)
            except OSError:
                pass

    def _recording_stop_completion_ready(self, source, condition, generation, completion):
        read_fd = self._recording_completion_read_fd
        self._recording_completion_source = None
        self._recording_completion_read_fd = None
        if read_fd is not None:
            try:
                os.read(read_fd, 64)
            except OSError:
                pass
            finally:
                try:
                    os.close(read_fd)
                except OSError:
                    pass
        session = self._recording_diagnostic_session
        if generation != self._recording_generation:
            if session is not None:
                session.diagnostic(
                    "recording_stop_completion_stale",
                    generation=generation,
                    current_generation=self._recording_generation,
                )
            return False
        if session is not None:
            session.diagnostic(
                "recording_stop_completion_observed_on_main_thread",
                generation=generation,
                condition=int(condition),
                error=str(completion.get("error")) if completion.get("error") is not None else None,
            )
        self._recording_finished(
            completion.get("interactions"),
            completion.get("error"),
        )
        return False

    def _prepare_recording_stop_ui(self):
        session = self._recording_diagnostic_session
        if session is not None:
            session.diagnostic("recording_stop_ui_cleanup_started")
        self._clear_recording_highlights()
        # The recording control now lives in the persistent Test Plan window;
        # stop no longer hides, shows, destroys, or recreates a GTK toplevel.
        if session is not None:
            session.diagnostic("recording_stop_ui_cleanup_finished")
        return False

    def _ensure_review_repository(self, repository, path):
        """Create/load the assigned repository only when review needs to mutate it."""
        if repository is not None:
            return repository, path
        self.plan, path = ensure_default_repository(self.plan, self.path)
        repository = ComponentRepository.load((path,))
        self.assigned_repository_path = assigned_repository_path(self.plan, self.path)
        # Do not publish the plan-owned recording fallback in the project's
        # repository catalogue. It becomes visible only after an explicit
        # repository assignment.
        if self.project_context and self.assigned_repository_path is not None:
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
        diagnostic_session = self._recording_diagnostic_session
        if diagnostic_session is not None:
            diagnostic_session.diagnostic(
                "recording_finished_entered",
                interaction_count=len(interactions or ()),
                error=str(error) if error is not None else None,
            )
        with _ACTIVE_RECORDING_LOCK:
            self._recording_stop_pending = False
            if _ACTIVE_RECORDING_WINDOW is self:
                _ACTIVE_RECORDING_WINDOW = None
        self._set_recording_toggle_state(active=False)
        diagnostic_path = getattr(diagnostic_session, "diagnostic_path", None)
        if error is not None:
            if diagnostic_session is not None:
                diagnostic_session.diagnostic_exception("recording_finish_failed", error)
            self.set_status("Recording failed")
            self.error("Recording", "%s: %s" % (type(error).__name__, error))
            if diagnostic_session is not None:
                diagnostic_session.diagnostic("recording_finished_exited", outcome="error")
            self._recording_diagnostic_session = None
            return False

        resolved = []
        unresolved = []
        captured_ids = set()
        provisional_ids = set()
        assigned_path = recording_repository_path(self.plan, self.path)
        assigned_repository = (
            ComponentRepository.load((assigned_path,))
            if assigned_path and assigned_path.exists()
            else None
        )
        recording_repository = getattr(diagnostic_session, "repository", None)

        # Menu opening is capture evidence, not an executable click. Retain its
        # durable owner even when the traversal is cancelled or no terminal
        # item is observed during this recording.
        owners = diagnostic_session.captured_menu_owners() if diagnostic_session else ()
        for owner_capture in owners:
            try:
                if assigned_repository is None:
                    assigned_repository, assigned_path = self._ensure_review_repository(
                        assigned_repository, assigned_path,
                    )
                assigned_repository, owner_id, created = materialize_captured_target(
                    assigned_repository, owner_capture,
                )
                if created:
                    captured_ids.add(owner_id)
            except Exception as exc:
                if diagnostic_session is not None:
                    diagnostic_session.diagnostic_exception(
                        "review_menu_owner_failed", exc, owner=owner_capture,
                    )

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

        assigned_for_resolution = assigned_repository_path(self.plan, self.path)
        if assigned_repository is not None and assigned_path is not None:
            assigned_repository.save(assigned_path)

        # Recording completion must not enter a nested GTK main loop.  The
        # previous synchronous Recorded Step Details dialog used Gtk.Dialog.run()
        # here, inside the GLib completion callback, and could leave the authoring
        # UI apparently frozen at "Stopping recording…" when the modal was
        # obscured or failed to map.  Keep recorded step metadata as generated;
        # optional editing belongs in the normal Step Builder after completion.
        if diagnostic_session is not None:
            diagnostic_session.diagnostic(
                "recording_review_steps_ready",
                resolved_count=len(resolved),
            )
        refresh_needed = False
        if resolved or captured_ids:
            if diagnostic_session is not None:
                diagnostic_session.diagnostic("recording_plan_update_started")
            if resolved:
                self.plan = replace(self.plan, steps=(*self.plan.steps, *resolved))
                if diagnostic_session is not None:
                    diagnostic_session.diagnostic(
                        "recording_plan_steps_appended",
                        added_count=len(resolved),
                        total_count=len(self.plan.steps),
                    )
            if assigned_repository is not None:
                if diagnostic_session is not None:
                    diagnostic_session.diagnostic(
                        "recording_repository_apply_started",
                        assigned_count=len(assigned_repository.components),
                        has_assigned_path=assigned_for_resolution is not None,
                    )
                if assigned_for_resolution is None:
                    # No external repository was assigned when recording began.
                    # Preserve the recorded repository as a portable plan snapshot.
                    # Do not compose it with the plan snapshot again: the captured
                    # repository already contains the objects resolved for this
                    # recording session.
                    self.plan = embed_plan_repository(self.plan, assigned_repository)
                    self.repository = assigned_repository
                    if diagnostic_session is not None:
                        diagnostic_session.diagnostic(
                            "recording_embedded_repository_applied",
                            repository_count=len(self.repository.components),
                        )
                else:
                    # An assigned repository is the live authoring source of truth.
                    # Re-overlaying repository_from_plan(self.plan) here composes a
                    # stale portable snapshot back onto its source and can trigger
                    # duplicate/conflicting identity work during recording finish.
                    self.repository = assigned_repository
                    if diagnostic_session is not None:
                        diagnostic_session.diagnostic(
                            "recording_assigned_repository_applied",
                            repository_count=len(self.repository.components),
                        )
                if self.registry_resources:
                    if diagnostic_session is not None:
                        diagnostic_session.diagnostic(
                            "recording_registry_overlay_started",
                            base_count=len(self.repository.components),
                            registry_count=len(self.registry_resources.repository.components),
                        )
                    self.repository = self.repository.overlay(self.registry_resources.repository)
                    if diagnostic_session is not None:
                        diagnostic_session.diagnostic(
                            "recording_registry_overlay_finished",
                            repository_count=len(self.repository.components),
                        )
                if diagnostic_session is not None:
                    diagnostic_session.diagnostic("recording_repository_apply_finished")
            if diagnostic_session is not None:
                diagnostic_session.diagnostic("recording_mark_dirty_started")
            self.mark_dirty()
            if diagnostic_session is not None:
                diagnostic_session.diagnostic("recording_mark_dirty_finished")
            refresh_needed = True
            if diagnostic_session is not None:
                diagnostic_session.diagnostic("recording_plan_update_finished")
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
        if diagnostic_session is not None:
            diagnostic_session.diagnostic("recording_finished_exited", outcome="complete")
        self._recording_diagnostic_session = None
        if refresh_needed:
            # Let the recording-completion callback unwind before mutating GTK
            # list/tree models.  refresh_all() clears models whose selection
            # signals can synchronously run additional authoring callbacks.
            GLib.idle_add(self._refresh_after_recording, diagnostic_session)
        return False

    def _refresh_after_recording(self, diagnostic_session):
        try:
            if diagnostic_session is not None:
                diagnostic_session.diagnostic("recording_refresh_started")
                diagnostic_session.diagnostic("recording_refresh_library_started")
            self.refresh_library()
            if diagnostic_session is not None:
                diagnostic_session.diagnostic("recording_refresh_library_finished")
                diagnostic_session.diagnostic("recording_refresh_objects_started")
            self.refresh_objects()
            if diagnostic_session is not None:
                diagnostic_session.diagnostic("recording_refresh_objects_finished")
                diagnostic_session.diagnostic("recording_refresh_flow_started")
            self.refresh_flow()
            if diagnostic_session is not None:
                diagnostic_session.diagnostic("recording_refresh_flow_finished")
                diagnostic_session.diagnostic("recording_refresh_variables_started")
            self.refresh_variables()
            if diagnostic_session is not None:
                diagnostic_session.diagnostic("recording_refresh_variables_finished")
                diagnostic_session.diagnostic("recording_refresh_finished")
        except Exception as exc:
            if diagnostic_session is not None:
                diagnostic_session.diagnostic_exception("recording_refresh_failed", exc)
            self.error("Recording Refresh", "%s: %s" % (type(exc).__name__, exc))
        return False

    def _review_recorded_step_details(self, calls):
        """Offer optional author-facing metadata before adding recorded calls."""
        dialog = Gtk.Dialog(title="Recorded Step Details", transient_for=self.window, modal=True)
        dialog.add_buttons("Use Defaults", Gtk.ResponseType.CANCEL, "Add Steps", Gtk.ResponseType.OK)
        scroll = Gtk.ScrolledWindow()
        scroll.set_size_request(650, min(520, max(180, len(calls) * 145)))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_border_width(10)
        scroll.add(box)
        dialog.get_content_area().pack_start(scroll, True, True, 0)
        fields = []
        for call in calls:
            name = call.name or call.description or call.step_id
            frame = Gtk.Frame(label=call.node_id)
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            column.set_border_width(6)
            frame.add(column)
            column.pack_start(Gtk.Label(label="Name", xalign=0), False, False, 0)
            entry = Gtk.Entry()
            entry.set_text(name)
            column.pack_start(entry, False, False, 0)
            column.pack_start(Gtk.Label(label="Description (optional)", xalign=0), False, False, 0)
            description = Gtk.TextView()
            description.set_wrap_mode(Gtk.WrapMode.WORD)
            description.get_buffer().set_text("")
            description.set_size_request(-1, 55)
            column.pack_start(description, False, False, 0)
            box.pack_start(frame, False, False, 0)
            fields.append((entry, description))
        dialog.show_all()
        response = dialog.run()
        result = []
        for call, (entry, description) in zip(calls, fields):
            buffer = description.get_buffer()
            result.append(replace(
                call,
                name=(entry.get_text().strip() or call.description or call.step_id)
                if response == Gtk.ResponseType.OK else (call.description or call.step_id),
                description=buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True).strip()
                if response == Gtk.ResponseType.OK else "",
            ))
        dialog.destroy()
        return result
