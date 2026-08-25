from __future__ import annotations

import json
import threading
import time
import tkinter as tk
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any

from automation_harness.backends.reference import ReferenceBackend
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.object_capture import ObjectCaptureService
from automation_harness.core.visual_baselines import approve_visual_candidate, reject_visual_candidate
from automation_harness.core.step_registry import default_step_registry
from automation_harness.core.test_plan import derive_execution_state, load_plan, save_plan, validate_plan, validate_plan_components
from automation_harness.models.plan import PlanVariableRef, StepCall, TestPlan
from automation_harness.runner.plan_execution import execute_plan


class AuthoringApp:
    """Local desktop authoring client over the framework's core models."""

    def __init__(self, root: tk.Tk, repository_path: Path | None = None, *, mode: str = "author") -> None:
        self.root = root
        self.mode = mode
        self.root.title({"capture": "Automation Harness Object Capture", "repository": "Automation Harness Object Repository"}.get(mode, "Automation Harness Author"))
        self.root.geometry("1180x760")
        self.registry = default_step_registry()
        self.capture = ObjectCaptureService()
        self.repository_path = repository_path
        self.repository = self._load_repository()
        self.plan = TestPlan(name="new-test-plan")
        self.selected_step: str | None = None
        self._run_active = False
        self._click_capture_active = False
        self._click_picker: tk.Toplevel | None = None
        self._click_picker_timeout: str | None = None
        self._last_capture = None
        self._highlight_windows: list[tk.Toplevel] = []
        self._highlight_after: str | None = None
        self.last_run_dir: Path | None = None
        self._build()
        self.refresh_all()

    def _load_repository(self) -> ComponentRepository:
        package_repo = Path(__file__).resolve().parents[1] / "resources" / "components.yaml"
        paths = [package_repo]
        if self.repository_path is not None:
            paths.append(self.repository_path)
        return ComponentRepository.load(paths)

    def _build(self) -> None:
        toolbar = ttk.Frame(self.root, padding=6)
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="Open Repository", command=self.open_repository).pack(side="left")
        ttk.Button(toolbar, text="Open Plan", command=self.open_plan_dialog).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="Save Plan", command=self.save_plan_dialog).pack(side="left", padx=4)
        ttk.Button(toolbar, text="Validate Plan", command=self.validate_plan_dialog).pack(side="left", padx=4)
        self.run_reference_button = ttk.Button(toolbar, text="Run Reference", command=self.run_reference_plan)
        self.run_reference_button.pack(side="left", padx=4)
        self.status = ttk.Label(toolbar, text="Ready")
        self.status.pack(side="right")

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=6, pady=(0, 6))

        self.notebook = notebook
        self.objects_tab = ttk.Frame(notebook)
        self.steps_tab = ttk.Frame(notebook)
        self.plan_tab = ttk.Frame(notebook)
        self.vars_tab = ttk.Frame(notebook)
        self.state_tab = ttk.Frame(notebook)
        notebook.add(self.objects_tab, text="Object Repository")
        notebook.add(self.steps_tab, text="Step Library")
        notebook.add(self.plan_tab, text="Test Composer")
        notebook.add(self.vars_tab, text="Variables")
        notebook.add(self.state_tab, text="Execution State")

        self._build_objects()
        if self.mode == "capture":
            return
        if self.mode == "repository":
            return
        self._build_steps()
        self._build_plan()
        self._build_variables()
        self._build_state()

    def _build_objects(self) -> None:
        left = ttk.Frame(self.objects_tab, padding=6)
        left.pack(side="left", fill="y")
        self.object_tree = ttk.Treeview(left, columns=("revision", "type", "actions"), show="tree headings", height=24)
        self.object_tree.heading("#0", text="Component")
        self.object_tree.heading("revision", text="Rev")
        self.object_tree.heading("type", text="Type")
        self.object_tree.heading("actions", text="Actions")
        self.object_tree.column("#0", width=260)
        self.object_tree.column("revision", width=50, anchor="center")
        self.object_tree.column("type", width=110)
        self.object_tree.column("actions", width=140)
        self.object_tree.pack(fill="both", expand=True)
        self.object_tree.bind("<<TreeviewSelect>>", lambda _e: self.show_object())
        self.object_tree.bind("<Button-3>", self._open_object_context_menu)
        self.object_context_menu = tk.Menu(self.root, tearoff=False)
        self.object_context_menu.add_command(label="Highlight", command=self.highlight_selected_object)

        buttons = ttk.Frame(left)
        buttons.pack(fill="x", pady=6)
        ttk.Button(buttons, text="Inspect", command=self.show_object).pack(side="left")
        if self.mode != "repository":
            ttk.Button(buttons, text="Capture Next Click", command=self.capture_next_click).pack(side="left", padx=(4, 0))
            ttk.Button(buttons, text="Capture at Pointer (2s)", command=self.capture_pointer_delayed).pack(side="left", padx=4)
            ttk.Button(buttons, text="Capture by Locator", command=self.capture_by_locator).pack(side="left")
            self.highlight_button = ttk.Button(buttons, text="Highlight Last Capture", command=self.highlight_last_capture, state="disabled")
            self.highlight_button.pack(side="left", padx=4)
            ttk.Button(buttons, text="Approve Visual Candidate", command=self.approve_visual_candidate).pack(side="left", padx=4)
            ttk.Button(buttons, text="Reject Visual Candidate", command=self.reject_visual_candidate).pack(side="left")
        if self.mode != "capture":
            ttk.Button(buttons, text="Edit Selected", command=self.edit_selected_object).pack(side="left", padx=4)

        right = ttk.Frame(self.objects_tab, padding=6)
        right.pack(side="left", fill="both", expand=True)
        self.object_detail = tk.Text(right, wrap="none")
        self.object_detail.pack(fill="both", expand=True)

    def _build_steps(self) -> None:
        left = ttk.Frame(self.steps_tab, padding=6)
        left.pack(side="left", fill="both", expand=True)
        self.step_tree = ttk.Treeview(left, columns=("domain", "signature"), show="tree headings")
        self.step_tree.heading("#0", text="Step")
        self.step_tree.heading("domain", text="Domain")
        self.step_tree.heading("signature", text="Signature")
        self.step_tree.column("#0", width=300)
        self.step_tree.column("domain", width=120)
        self.step_tree.column("signature", width=420)
        self.step_tree.pack(fill="both", expand=True)
        self.step_tree.bind("<<TreeviewSelect>>", lambda _e: self.show_step())
        self.step_tree.bind("<Double-1>", lambda _e: self.add_selected_step())
        ttk.Button(left, text="Add Selected Step to Plan", command=self.add_selected_step).pack(anchor="w", pady=6)
        right = ttk.Frame(self.steps_tab, padding=6)
        right.pack(side="left", fill="both", expand=True)
        self.step_detail = tk.Text(right, width=55)
        self.step_detail.pack(fill="both", expand=True)

    def _build_plan(self) -> None:
        top = ttk.Frame(self.plan_tab, padding=6)
        top.pack(fill="x")
        ttk.Label(top, text="Plan name:").pack(side="left")
        self.plan_name = tk.StringVar(value=self.plan.name)
        ttk.Entry(top, textvariable=self.plan_name, width=40).pack(side="left", padx=4)
        ttk.Button(top, text="Edit Selected", command=self.edit_plan_step).pack(side="left", padx=4)
        ttk.Button(top, text="Remove Selected", command=self.remove_plan_step).pack(side="left")
        self.plan_tree = ttk.Treeview(
            self.plan_tab,
            columns=("step", "inputs", "outputs", "depends"),
            show="headings",
        )
        for key, label, width in (
            ("step", "Registered Step", 260),
            ("inputs", "Inputs", 330),
            ("outputs", "Output Bindings", 250),
            ("depends", "Depends", 140),
        ):
            self.plan_tree.heading(key, text=label)
            self.plan_tree.column(key, width=width)
        self.plan_tree.pack(fill="both", expand=True, padx=6, pady=(0, 6))

    def _build_variables(self) -> None:
        frame = ttk.Frame(self.vars_tab, padding=6)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Plan globals (JSON object)").pack(anchor="w")
        self.variables_text = tk.Text(frame)
        self.variables_text.pack(fill="both", expand=True)
        ttk.Button(frame, text="Apply Variables", command=self.apply_variables).pack(anchor="w", pady=6)

    def _build_state(self) -> None:
        frame = ttk.Frame(self.state_tab, padding=6)
        frame.pack(fill="both", expand=True)
        self.state_caption = ttk.Label(frame, text="Pre-execution managed queue projection")
        self.state_caption.pack(anchor="w")
        self.state_tree = ttk.Treeview(frame, columns=("step", "status", "waiting"), show="headings")
        self.state_tree.heading("step", text="Step")
        self.state_tree.heading("status", text="Status")
        self.state_tree.heading("waiting", text="Unresolved Variables")
        self.state_tree.column("step", width=320)
        self.state_tree.column("status", width=100)
        self.state_tree.column("waiting", width=420)
        self.state_tree.pack(fill="both", expand=True)
        ttk.Button(frame, text="Refresh Projection", command=self.refresh_state).pack(anchor="w", pady=6)

    def refresh_all(self) -> None:
        self.refresh_objects()
        if self.mode != "author":
            return
        self.refresh_steps()
        self.refresh_plan()
        self.refresh_variables()
        self.refresh_state()

    def refresh_objects(self) -> None:
        self.object_tree.delete(*self.object_tree.get_children())
        for component_id, definition in sorted(self.repository.components.items()):
            self.object_tree.insert("", "end", iid=component_id, text=component_id, values=(definition.revision, definition.object_type.value, ",".join(sorted(item.value for item in definition.semantic_actions))))

    def show_object(self) -> None:
        selected = self.object_tree.selection()
        if not selected:
            return
        definition = self.repository.get(selected[0])
        payload = {
            "component_id": definition.component_id,
            "description": definition.description,
            "revision": definition.revision,
            "actions": sorted(definition.actions),
            "semantic_actions": sorted(item.value for item in definition.semantic_actions),
            "object_type": definition.object_type.value,
            "framework": definition.framework,
            "native_class": definition.native_class,
            "properties": dict(definition.properties),
            "subobjects": {key: dict(value) for key, value in definition.subobjects.items()},
            "expected_states": dict(definition.expected_states),
            "visual": dict(definition.visual) if definition.visual else None,
            "strategies": [{"type": item.type, **item.options} for item in definition.strategies],
        }
        self._set_text(self.object_detail, json.dumps(payload, indent=2, default=str))

    def edit_selected_object(self) -> None:
        selected = self.object_tree.selection()
        if not selected:
            messagebox.showinfo("Object Repository", "Select a component to edit.")
            return
        if self.repository_path is None:
            messagebox.showerror("Object Repository", "Open or create an editable repository first.")
            return
        component_id = selected[0]
        definition = self.repository.get(component_id)
        document = ComponentRepository({component_id: definition}).to_document()["components"][component_id]
        raw = simpledialog.askstring("Edit component", "Component definition JSON:", initialvalue=json.dumps(document, indent=2))
        if raw is None:
            return
        try:
            value = json.loads(raw)
            parsed = ComponentRepository.from_document({"version": 1, "components": {component_id: value}}, source="editor")
            editable = ComponentRepository.load([self.repository_path]) if self.repository_path.exists() else ComponentRepository({})
            editable.with_component(parsed.get(component_id)).save(self.repository_path)
            self.repository = self._load_repository()
            self.refresh_objects()
            self.object_tree.selection_set(component_id)
            self.show_object()
            self.status.configure(text=f"Saved {component_id}")
        except Exception as exc:
            messagebox.showerror("Object Repository", f"{type(exc).__name__}: {exc}")

    def capture_pointer_delayed(self) -> None:
        if not self.capture.available:
            messagebox.showerror("AT-SPI unavailable", "pyatspi is not installed on this host.")
            return
        self.status.configure(text="Move pointer over target object…")
        self.root.after(2000, self._capture_pointer_now)

    def capture_next_click(self) -> None:
        """Hide the authoring UI and intercept exactly one desktop click."""
        if not self.capture.available:
            messagebox.showerror("AT-SPI unavailable", "pyatspi is not installed on this host.")
            return
        if self._click_capture_active:
            return
        self._click_capture_active = True
        self.status.configure(text="Click the target object within 30 seconds…")
        self.root.withdraw()
        self.root.after(150, self._show_click_picker)

    def _show_click_picker(self) -> None:
        if not self._click_capture_active:
            return
        picker = tk.Toplevel(self.root)
        self._click_picker = picker
        picker.overrideredirect(True)
        picker.geometry(f"{picker.winfo_screenwidth()}x{picker.winfo_screenheight()}+0+0")
        picker.configure(cursor="crosshair", background="black")
        picker.attributes("-topmost", True)
        # A nearly transparent real window reliably owns the next click on
        # X11, Xwayland, and Windows without requiring a privileged global
        # mouse hook. The click is consumed, so the inspected application
        # cannot mutate or replace its accessibility object before capture.
        picker.attributes("-alpha", 0.01)
        # Wait for release so both halves of the physical click remain owned
        # by the picker; destroying on press can deliver the release to the
        # underlying control and accidentally activate it.
        picker.bind("<ButtonRelease-1>", self._click_picker_selected)
        picker.bind("<Escape>", lambda _event: self._cancel_click_picker())
        picker.lift()
        picker.focus_force()
        picker.grab_set_global()
        self._click_picker_timeout = self.root.after(30_000, self._click_picker_timed_out)

    def _click_picker_selected(self, event: tk.Event) -> None:
        point = (int(event.x_root), int(event.y_root))
        self._destroy_click_picker()
        # Let the transparent native window disappear before querying the
        # accessibility tree underneath its desktop coordinate.
        self.root.after(120, lambda: self._capture_click_point(point))

    def _capture_click_point(self, point: tuple[int, int]) -> None:
        threading.Thread(
            target=self._resolve_click_point,
            args=point,
            name="automation-object-capture",
            daemon=True,
        ).start()

    def _resolve_click_point(self, x: int, y: int) -> None:
        try:
            captured = self.capture.capture_scoped_at_point(x, y)
        except Exception as exc:
            self.root.after(0, lambda error=exc: self._finish_next_click_capture(error=error))
        else:
            self.root.after(0, lambda result=captured: self._finish_next_click_capture(captured=result))

    def _click_picker_timed_out(self) -> None:
        self._destroy_click_picker()
        self._finish_next_click_capture(error=TimeoutError("no mouse button press was received before capture timed out"))

    def _cancel_click_picker(self) -> None:
        self._destroy_click_picker()
        self._finish_next_click_capture(error=RuntimeError("capture cancelled"))

    def _destroy_click_picker(self) -> None:
        if self._click_picker_timeout is not None:
            self.root.after_cancel(self._click_picker_timeout)
            self._click_picker_timeout = None
        picker, self._click_picker = self._click_picker, None
        if picker is not None:
            try:
                picker.grab_release()
            except tk.TclError:
                pass
            picker.destroy()

    def _finish_next_click_capture(self, *, captured=None, error: Exception | None = None) -> None:
        self._click_capture_active = False
        if error is not None:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
            self.status.configure(text="Ready")
            messagebox.showerror("Capture failed", f"{type(error).__name__}: {error}")
            return
        self.status.configure(text="Captured clicked object")
        self._show_highlight_then_present(captured)

    def _capture_pointer_now(self) -> None:
        try:
            captured = self.capture.capture_at_point(self.root.winfo_pointerx(), self.root.winfo_pointery())
            self._present_capture(captured)
        except Exception as exc:
            messagebox.showerror("Capture failed", f"{type(exc).__name__}: {exc}")
        finally:
            self.status.configure(text="Ready")

    def capture_by_locator(self) -> None:
        if not self.capture.available:
            messagebox.showerror("AT-SPI unavailable", "pyatspi is not installed on this host.")
            return
        name = simpledialog.askstring("Capture by locator", "Accessible name (blank allowed):")
        role = simpledialog.askstring("Capture by locator", "Role (blank allowed):")
        accessible_id = simpledialog.askstring("Capture by locator", "Accessible ID (blank allowed):")
        if not name and not role and not accessible_id:
            return
        try:
            captured = self.capture.capture_by_locator(
                name=name or None,
                role=role or None,
                accessible_id=accessible_id or None,
            )
            self._present_capture(captured)
        except Exception as exc:
            messagebox.showerror("Capture failed", f"{type(exc).__name__}: {exc}")

    def _present_capture(self, captured) -> None:
        self._last_capture = captured
        if hasattr(self, "highlight_button"):
            self.highlight_button.configure(state="normal" if captured.bounds else "disabled")
        assessments = [item.to_dict() for item in self.capture.assess(captured)]
        self._set_text(self.object_detail, json.dumps({"capture": captured.to_dict(), "locator_assessments": assessments}, indent=2, default=str))
        if self.repository_path is None:
            if not messagebox.askyesno("Save capture", "No editable repository is open. Choose a repository file now?"):
                return
            path = filedialog.asksaveasfilename(defaultextension=".yaml", filetypes=[("YAML", "*.yaml *.yml")])
            if not path:
                return
            self.repository_path = Path(path)
        component_id = simpledialog.askstring("Save capture", "Logical component ID:")
        if not component_id:
            return
        try:
            if captured.candidate_strategy().type == "anchored_visual":
                definition = self.capture.save_capture(self.repository_path, component_id, captured)
            else:
                candidate = captured.candidate_identification().to_dict()
                identity_raw = simpledialog.askstring(
                    "Object identification",
                    "AT-SPI identity JSON. Mandatory properties always match; assistive properties are applied in order only if needed. Add ordinal only as an explicit last resort.",
                    initialvalue=json.dumps(candidate, separators=(",", ":")),
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
            messagebox.showerror("Object identification", f"{type(exc).__name__}: {exc}")
            return
        self.repository = self._load_repository()
        self.refresh_objects()
        self.status.configure(text=f"Saved {definition.component_id} revision {definition.revision}")
        if captured.bounds and messagebox.askyesno("Visual capture", "Stage a component-bounds visual candidate now?"):
            try:
                result = self.capture.stage_visual_capture(self.repository_path, component_id, captured)
                self.status.configure(text=f"Staged visual candidate: {result['variant_key']}")
                messagebox.showinfo("Visual candidate", json.dumps(result, indent=2, default=str))
            except Exception as exc:
                messagebox.showerror("Visual capture", f"{type(exc).__name__}: {exc}")

    def highlight_last_capture(self) -> None:
        """Temporarily hide the editor and outline the most recently captured object."""
        if self._last_capture is None or self._last_capture.bounds is None:
            messagebox.showinfo("Highlight capture", "Capture an object with screen bounds first.")
            return
        self.root.withdraw()
        self._show_highlight(self._last_capture.bounds, restore_editor=True)

    def _show_highlight_then_present(self, captured) -> None:
        self._last_capture = captured
        if not captured.bounds:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
            self._present_capture(captured)
            return
        self.root.withdraw()
        self._show_highlight(captured.bounds, restore_editor=False)
        self.root.after(1600, lambda result=captured: self._restore_after_highlight(result))

    def _show_highlight(self, bounds: tuple[int, int, int, int], *, restore_editor: bool) -> None:
        self._clear_highlight()
        for x, y, width, height in _highlight_rectangles(bounds):
            edge = tk.Toplevel(self.root)
            edge.overrideredirect(True)
            edge.configure(background="#ff3b30")
            try:
                edge.attributes("-topmost", True)
                edge.attributes("-alpha", 0.88)
            except tk.TclError:
                pass
            edge.geometry(f"{width}x{height}+{x}+{y}")
            edge.deiconify()
            self._highlight_windows.append(edge)
        if restore_editor:
            self._highlight_after = self.root.after(1600, self._restore_after_highlight)

    def _restore_after_highlight(self, captured=None) -> None:
        self._clear_highlight()
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        if captured is not None:
            self._present_capture(captured)

    def _clear_highlight(self) -> None:
        if self._highlight_after is not None:
            self.root.after_cancel(self._highlight_after)
            self._highlight_after = None
        for edge in self._highlight_windows:
            try:
                edge.destroy()
            except tk.TclError:
                pass
        self._highlight_windows.clear()

    def _open_object_context_menu(self, event) -> None:
        item_id = self.object_tree.identify_row(event.y)
        if not item_id:
            return
        self.object_tree.selection_set(item_id)
        self.object_tree.focus(item_id)
        self.object_context_menu.tk_popup(event.x_root, event.y_root)

    def highlight_selected_object(self) -> None:
        selected = self.object_tree.selection()
        if not selected:
            return
        if not self.capture.available:
            messagebox.showerror("Highlight", "pyatspi is not installed on this host.")
            return
        definition = self.repository.get(selected[0])
        self.status.configure(text=f"Resolving {definition.component_id} for highlight…")
        threading.Thread(
            target=self._resolve_selected_for_highlight,
            args=(definition,),
            name="automation-object-highlight",
            daemon=True,
        ).start()

    def _resolve_selected_for_highlight(self, definition) -> None:
        deadline = time.monotonic() + 5.0
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            for strategy in definition.strategies:
                if strategy.type == "anchored_visual":
                    try:
                        captured = self.capture.resolve_anchored_visual(strategy.options)
                    except Exception as exc:
                        last_error = exc
                        continue
                    self.root.after(0, lambda result=captured: self._finish_repository_highlight(result))
                    return
                if strategy.type not in {"atspi", "java_accessibility"}:
                    continue
                try:
                    captured = self.capture.capture_by_locator(
                        identification=strategy.options.get("identification"),
                    )
                except Exception as exc:
                    last_error = exc
                    continue
                self.root.after(0, lambda result=captured: self._finish_repository_highlight(result))
                return
            time.sleep(0.2)
        detail = f" Last error: {last_error}" if last_error else ""
        error = LookupError(f"No live object matched {definition.component_id!r} within 5 seconds.{detail}")
        self.root.after(0, lambda result=error: self._finish_repository_highlight(error=result))

    def _finish_repository_highlight(self, captured=None, error: Exception | None = None) -> None:
        if error is not None:
            self.status.configure(text="Ready")
            messagebox.showerror("Highlight failed", str(error))
            return
        self._last_capture = captured
        if hasattr(self, "highlight_button"):
            self.highlight_button.configure(state="normal" if captured.bounds else "disabled")
        if captured.bounds is None:
            self.status.configure(text="Resolved object has no screen bounds")
            messagebox.showerror("Highlight failed", "The resolved object has no screen bounds to highlight.")
            return
        self.status.configure(text="Highlighting resolved object")
        self.root.withdraw()
        self._show_highlight(captured.bounds, restore_editor=True)

    def approve_visual_candidate(self) -> None:
        if self.repository_path is None:
            messagebox.showerror("Visual approval", "Open an editable repository first.")
            return
        selected = self.object_tree.selection()
        if not selected:
            messagebox.showinfo("Visual approval", "Select a component first.")
            return
        key = simpledialog.askstring("Approve visual candidate", "Variant key:")
        if not key:
            return
        mask_path = None
        if messagebox.askyesno("Visual mask", "Attach or replace a grayscale mask for this baseline?"):
            selected_mask = filedialog.askopenfilename(filetypes=[("PNG images", "*.png")])
            if not selected_mask:
                return
            mask_path = Path(selected_mask)
        try:
            definition = approve_visual_candidate(self.repository_path, selected[0], key, mask=mask_path)
            self.repository = self._load_repository()
            self.refresh_objects()
            self.status.configure(text=f"Approved visual revision {definition.visual['revision']}")
        except Exception as exc:
            messagebox.showerror("Visual approval", f"{type(exc).__name__}: {exc}")

    def reject_visual_candidate(self) -> None:
        if self.repository_path is None:
            messagebox.showerror("Visual rejection", "Open an editable repository first.")
            return
        selected = self.object_tree.selection()
        if not selected:
            messagebox.showinfo("Visual rejection", "Select a component first.")
            return
        key = simpledialog.askstring("Reject visual candidate", "Variant key:")
        if not key:
            return
        try:
            reject_visual_candidate(self.repository_path, selected[0], key)
            self.status.configure(text=f"Rejected visual candidate {key}")
        except Exception as exc:
            messagebox.showerror("Visual rejection", f"{type(exc).__name__}: {exc}")

    def refresh_steps(self) -> None:
        self.step_tree.delete(*self.step_tree.get_children())
        for definition in self.registry.definitions():
            self.step_tree.insert("", "end", iid=definition.name, text=definition.name, values=(definition.domain, str(definition.invocation_signature)))

    def show_step(self) -> None:
        selected = self.step_tree.selection()
        if not selected:
            return
        self.selected_step = selected[0]
        definition = self.registry.get(self.selected_step)
        self._set_text(self.step_detail, json.dumps(definition.to_dict(), indent=2, default=str))

    def add_selected_step(self) -> None:
        selected = self.step_tree.selection()
        if not selected:
            return
        definition = self.registry.get(selected[0])
        node_id = f"step-{len(self.plan.steps) + 1:03d}"
        inputs = {}
        for item in definition.inputs:
            if not item.required:
                inputs[item.name] = item.default
        call = StepCall(node_id=node_id, step_id=definition.name, inputs=inputs)
        self.plan = replace(self.plan, steps=(*self.plan.steps, call))
        self.refresh_plan()
        self.refresh_state()

    def edit_plan_step(self) -> None:
        selected = self.plan_tree.selection()
        if not selected:
            return
        node_id = selected[0]
        call = next(item for item in self.plan.steps if item.node_id == node_id)
        inputs_raw = simpledialog.askstring("Edit inputs", "Inputs JSON (use {\"$var\":\"name\"} for references):", initialvalue=json.dumps(_encode_gui(call.inputs)))
        if inputs_raw is None:
            return
        outputs_raw = simpledialog.askstring("Edit outputs", "Output bindings JSON:", initialvalue=json.dumps(dict(call.outputs)))
        if outputs_raw is None:
            return
        try:
            inputs = _decode_gui(json.loads(inputs_raw))
            outputs = json.loads(outputs_raw)
            if not isinstance(inputs, dict) or not isinstance(outputs, dict):
                raise ValueError("inputs and outputs must be JSON objects")
        except Exception as exc:
            messagebox.showerror("Invalid step data", str(exc))
            return
        updated = replace(call, inputs=inputs, outputs={str(k): str(v) for k, v in outputs.items()})
        self.plan = replace(self.plan, steps=tuple(updated if item.node_id == node_id else item for item in self.plan.steps))
        self.refresh_plan()
        self.refresh_state()

    def remove_plan_step(self) -> None:
        selected = self.plan_tree.selection()
        if not selected:
            return
        node_id = selected[0]
        self.plan = replace(self.plan, steps=tuple(item for item in self.plan.steps if item.node_id != node_id))
        self.refresh_plan()
        self.refresh_state()

    def refresh_plan(self) -> None:
        self.plan = replace(self.plan, name=self.plan_name.get().strip() or "new-test-plan")
        self.plan_tree.delete(*self.plan_tree.get_children())
        for call in self.plan.steps:
            self.plan_tree.insert(
                "", "end", iid=call.node_id,
                values=(call.step_id, json.dumps(_encode_gui(call.inputs), separators=(",", ":")), json.dumps(dict(call.outputs), separators=(",", ":")), ",".join(call.depends_on)),
            )

    def refresh_variables(self) -> None:
        self._set_text(self.variables_text, json.dumps(dict(self.plan.variables), indent=2, default=str))

    def apply_variables(self) -> None:
        try:
            values = json.loads(self.variables_text.get("1.0", "end").strip() or "{}")
            if not isinstance(values, dict):
                raise ValueError("variables must be a JSON object")
        except Exception as exc:
            messagebox.showerror("Invalid variables", str(exc))
            return
        self.plan = replace(self.plan, variables=values)
        self.refresh_state()

    def refresh_state(self) -> None:
        self.refresh_plan()
        self.state_caption.configure(text="Pre-execution managed queue projection")
        state = derive_execution_state(self.plan)
        self.state_tree.delete(*self.state_tree.get_children())
        for node_id, item in state.steps.items():
            self.state_tree.insert("", "end", iid=node_id, values=(item.step_id, item.status.value, ", ".join(item.unresolved_variables)))

    def validate_plan_dialog(self) -> None:
        self.refresh_plan()
        issues = validate_plan(self.plan, self.registry)
        issues.extend(validate_plan_components(self.plan, self.repository))
        if issues:
            messagebox.showerror("Plan validation", "\n".join(issues))
        else:
            messagebox.showinfo("Plan validation", "Plan is structurally valid against the current registered-step catalog.")

    def open_plan_dialog(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("YAML", "*.yaml *.yml"), ("All files", "*")])
        if not path:
            return
        try:
            plan = load_plan(Path(path))
        except Exception as exc:
            messagebox.showerror("Plan error", f"{type(exc).__name__}: {exc}")
            return
        self.plan = plan
        self.plan_name.set(plan.name)
        self.refresh_plan()
        self.refresh_variables()
        self.refresh_state()
        self.status.configure(text=f"Opened plan: {path}")

    def run_reference_plan(self) -> None:
        if self._run_active:
            return
        self.refresh_plan()
        issues = validate_plan(self.plan, self.registry)
        issues.extend(validate_plan_components(self.plan, self.repository))
        if issues:
            messagebox.showerror("Plan validation", "\n".join(issues))
            return
        self._run_active = True
        self.run_reference_button.state(["disabled"])
        self.status.configure(text="Running against isolated reference backend…")
        plan = self.plan
        runs_dir = (Path.cwd() / "runs").resolve()

        def worker() -> None:
            backend = ReferenceBackend(gui=True, display_mode="virtual")
            result = execute_plan(
                plan,
                backend,
                runs_dir=runs_dir,
                component_repository=self.repository,
            )
            self.root.after(0, lambda: self._present_reference_result(result))

        threading.Thread(target=worker, name="automation-reference-plan-run", daemon=True).start()

    def _present_reference_result(self, result) -> None:
        self._run_active = False
        self.run_reference_button.state(["!disabled"])
        self.last_run_dir = result.artifact_dir
        if result.artifact_dir is not None:
            state_path = Path(result.artifact_dir) / "execution_state.json"
            if state_path.is_file():
                try:
                    payload = json.loads(state_path.read_text(encoding="utf-8"))
                    self._show_execution_state(payload)
                except Exception:
                    self.refresh_state()
        status = "PASS" if result.exit_code == 0 else "FAIL"
        self.status.configure(text=f"{status}: reference run {result.run_id}")
        detail = f"Passed: {result.passed}\nFailed: {result.failed}\nExit code: {result.exit_code}"
        if result.validation_errors:
            detail += "\n\n" + "\n".join(result.validation_errors)
        if result.exit_code == 0:
            messagebox.showinfo("Reference run", detail)
        else:
            messagebox.showerror("Reference run", detail)

    def _show_execution_state(self, payload: dict[str, Any]) -> None:
        self.state_caption.configure(text="Last reference execution state")
        self.state_tree.delete(*self.state_tree.get_children())
        for node_id, item in payload.get("steps", {}).items():
            waiting = ", ".join(item.get("unresolved_variables", []))
            self.state_tree.insert(
                "",
                "end",
                iid=node_id,
                values=(item.get("step_id", ""), item.get("status", ""), waiting),
            )

    def save_plan_dialog(self) -> None:
        self.refresh_plan()
        issues = validate_plan(self.plan, self.registry)
        if issues and not messagebox.askyesno("Plan has validation issues", "\n".join(issues) + "\n\nSave anyway?"):
            return
        path = filedialog.asksaveasfilename(defaultextension=".yaml", filetypes=[("YAML", "*.yaml *.yml")])
        if path:
            save_plan(self.plan, Path(path))
            self.status.configure(text=f"Saved plan: {path}")

    def open_repository(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("YAML", "*.yaml *.yml"), ("All files", "*")])
        if not path:
            return
        self.repository_path = Path(path)
        try:
            self.repository = self._load_repository()
        except Exception as exc:
            messagebox.showerror("Repository error", str(exc))
            return
        self.refresh_objects()

    @staticmethod
    def _set_text(widget: tk.Text, value: str) -> None:
        widget.delete("1.0", "end")
        widget.insert("1.0", value)


def _encode_gui(value: Any) -> Any:
    if isinstance(value, PlanVariableRef):
        return {"$var": value.path}
    if isinstance(value, dict):
        return {key: _encode_gui(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_encode_gui(item) for item in value]
    return value


def _decode_gui(value: Any) -> Any:
    if isinstance(value, dict):
        if set(value) == {"$var"} and isinstance(value["$var"], str):
            return PlanVariableRef(value["$var"])
        return {key: _decode_gui(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode_gui(item) for item in value]
    return value


def _highlight_rectangles(
    bounds: tuple[int, int, int, int], *, thickness: int = 4,
) -> tuple[tuple[int, int, int, int], ...]:
    """Return four always-visible edge rectangles for a desktop-space bound."""
    x, y, width, height = bounds
    if width <= 0 or height <= 0:
        raise ValueError("highlight bounds require positive width and height")
    edge = max(1, min(thickness, width, height))
    return (
        (x, y, width, edge),
        (x, y + height - edge, width, edge),
        (x, y, edge, height),
        (x + width - edge, y, edge, height),
    )


def main(argv: list[str] | None = None) -> int:
    return _launch(argv, mode="author", prog="automation-author", description="Local automation authoring GUI")


def capture_main(argv: list[str] | None = None) -> int:
    return _launch(argv, mode="capture", prog="automation-capture", description="Object Capture / Object Spy GUI")


def repository_main(argv: list[str] | None = None) -> int:
    return _launch(argv, mode="repository", prog="automation-repository", description="Object Repository editor GUI")


def _launch(argv: list[str] | None, *, mode: str, prog: str, description: str) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog=prog, description=description)
    parser.add_argument("--repository", type=Path)
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="construct and render the authoring GUI once, then exit without entering the event loop",
    )
    args = parser.parse_args(argv)
    root = tk.Tk()
    AuthoringApp(root, args.repository, mode=mode)
    if args.smoke_test:
        root.update_idletasks()
        root.update()
        root.destroy()
        return 0
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
