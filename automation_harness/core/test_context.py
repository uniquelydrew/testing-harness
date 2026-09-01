from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from automation_harness.core.component_handle import ComponentHandle
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.services import AutomationServices
from automation_harness.core.step_registry import StepInvocationResult, StepRegistry, load_step_libraries
from automation_harness.core.variables import VariableRef, VariableStore
from automation_harness.reference.protocol import ReferenceClient
from automation_harness.utils.evidence import EvidenceRecorder


@dataclass
class TestContext:
    __test__ = False
    backend: str
    run_dir: Path
    evidence: EvidenceRecorder
    components: ComponentRepository
    capabilities: frozenset[str]
    steps: StepRegistry
    globals: VariableStore | None = None
    reference: ReferenceClient | None = None
    services: AutomationServices | None = None

    def __post_init__(self) -> None:
        if self.globals is None:
            self.globals = VariableStore(self.evidence)
        if self.services is None:
            if self.reference is not None:
                from automation_harness.reference.services import services_for_reference
                self.services = services_for_reference(self.reference)
            else:
                self.services = AutomationServices()

    @classmethod
    def from_environment(cls) -> "TestContext":
        import os

        backend = os.environ.get("AUTOMATION_HARNESS_BACKEND", "")
        run_dir_raw = os.environ.get("AUTOMATION_HARNESS_RUN_DIR")
        if not run_dir_raw:
            raise RuntimeError("AUTOMATION_HARNESS_RUN_DIR is not set; tests must be launched through automation-run")
        run_dir = Path(run_dir_raw)
        evidence = EvidenceRecorder(run_dir / "events.jsonl")

        capabilities_raw = os.environ.get("AUTOMATION_HARNESS_CAPABILITIES", "[]")
        try:
            capabilities_value = json.loads(capabilities_raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("AUTOMATION_HARNESS_CAPABILITIES is not valid JSON") from exc
        if not isinstance(capabilities_value, list) or not all(isinstance(item, str) for item in capabilities_value):
            raise RuntimeError("AUTOMATION_HARNESS_CAPABILITIES must encode a list of strings")
        capabilities = frozenset(capabilities_value)

        globals_raw = os.environ.get("AUTOMATION_HARNESS_GLOBALS", "{}")
        try:
            globals_value = json.loads(globals_raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("AUTOMATION_HARNESS_GLOBALS is not valid JSON") from exc
        if not isinstance(globals_value, dict):
            raise RuntimeError("AUTOMATION_HARNESS_GLOBALS must encode a mapping")
        global_variables = VariableStore(evidence, globals_value)

        package_components = Path(__file__).resolve().parents[1] / "resources" / "components.yaml"
        component_paths = [package_components]
        bundle_components = os.environ.get("AUTOMATION_HARNESS_COMPONENTS")
        if bundle_components:
            component_paths.append(Path(bundle_components))
        components = ComponentRepository.load(component_paths)

        step_libraries_raw = os.environ.get("AUTOMATION_HARNESS_STEP_LIBRARIES", "[]")
        try:
            step_library_values = json.loads(step_libraries_raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("AUTOMATION_HARNESS_STEP_LIBRARIES is not valid JSON") from exc
        if not isinstance(step_library_values, list) or not all(isinstance(item, str) for item in step_library_values):
            raise RuntimeError("AUTOMATION_HARNESS_STEP_LIBRARIES must encode a list of paths")
        steps = load_step_libraries(step_library_values)
        _write_step_catalog(run_dir, steps)

        if backend == "reference":
            socket_path = os.environ.get("AUTOMATION_HARNESS_SOCKET")
            if not socket_path:
                raise RuntimeError("reference backend selected without AUTOMATION_HARNESS_SOCKET")
            return cls(
                backend=backend,
                run_dir=run_dir,
                evidence=evidence,
                components=components,
                capabilities=capabilities,
                steps=steps,
                globals=global_variables,
                reference=ReferenceClient(socket_path),
            )
        if backend in {"gtk-demo", "java-desktop", "live-desktop"}:
            return cls(
                backend=backend,
                run_dir=run_dir,
                evidence=evidence,
                components=components,
                capabilities=capabilities,
                steps=steps,
                globals=global_variables,
            )
        raise RuntimeError(f"unsupported or unsafe backend in test context: {backend!r}")

    def component(self, component_id: str) -> ComponentHandle:
        return ComponentHandle(self, self.components.get(component_id))

    def run_step(
        self,
        step_name: str,
        /,
        *args: Any,
        bind_outputs: Mapping[str, str] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Invoke a registered reusable step by stable semantic name.

        ``VariableRef`` inputs are resolved immediately before invocation. Named
        outputs can be routed into test-global variables using ``bind_outputs``.
        """
        return self.steps.invoke(
            self,
            step_name,
            *args,
            bind_outputs=bind_outputs,
            **kwargs,
        )

    def run_step_detailed(
        self,
        step_name: str,
        /,
        *args: Any,
        bind_outputs: Mapping[str, str] | None = None,
        **kwargs: Any,
    ) -> StepInvocationResult:
        """Invoke a step and return its already-extracted transactional outputs."""
        return self.steps.invoke_detailed(
            self,
            step_name,
            *args,
            bind_outputs=bind_outputs,
            **kwargs,
        )

    def ref(self, path: str) -> VariableRef:
        """Create a deferred reference to a test-global variable or nested value."""
        assert self.globals is not None
        return self.globals.ref(path)

    def require_services(self) -> AutomationServices:
        assert self.services is not None
        return self.services

    def require_reference(self) -> ReferenceClient:
        if self.reference is None:
            raise RuntimeError("operation requires the synthetic reference backend")
        return self.reference


def _write_step_catalog(run_dir: Path, registry: StepRegistry) -> None:
    path = run_dir / "registered_steps.json"
    if path.exists():
        return
    payload = {
        "domains": list(registry.domains()),
        "steps": [definition.to_dict() for definition in registry.definitions()],
    }
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    try:
        temporary.replace(path)
    except FileNotFoundError:
        pass
