from __future__ import annotations

import json
import threading
import tkinter as tk
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any

from automation_harness.backends.reference import ReferenceBackend
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.object_capture import ObjectCaptureService
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
        self.object_tree = ttk.Treeview(left, columns=("revision", "actions"), show="tree headings", height=24)
        self.object_tree.heading("#0", text="Component")
        self.object_tree.heading("revision", text="Rev")
        self.object_tree.heading("actions", text="Actions")
        self.object_tree.column("#0", width=260)
        self.object_tree.column("revision", width=50, anchor="center")
        self.object_tree.column("actions", width=140)
        self.object_tree.pack(fill="both", expand=True)
        self.object_tree.bind("<<TreeviewSelect>>", lambda _e: self.show_object())

        buttons = ttk.Frame(left)
        buttons.pack(fill="x", pady=6)
        ttk.Button(buttons, text="Inspect", command=self.show_object).pack(side="left")
        if self.mode != "repository":
            ttk.Button(buttons, text="Capture at Pointer (2s)", command=self.capture_pointer_delayed).pack(side="left", padx=4)
            ttk.Button(buttons, text="Capture by Locator", command=self.capture_by_locator).pack(side="left")
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
            self.object_tree.insert("", "end", iid=component_id, text=component_id, values=(definition.revision, ",".join(sorted(definition.actions))))

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
            "expected_states": dict(definition.expected_states),
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
        candidate = captured.candidate_identification().to_dict()
        identity_raw = simpledialog.askstring(
            "Object identification",
            "AT-SPI identity JSON. Mandatory properties always match; assistive properties are applied in order only if needed. Add ordinal only as an explicit last resort.",
            initialvalue=json.dumps(candidate, separators=(",", ":")),
        )
        if identity_raw is None:
            return
        try:
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
