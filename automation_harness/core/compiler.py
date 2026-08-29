"""Deterministic compilation of semantic TestPlans into execution artifacts.

The semantic plan remains authoritative.  A compiled artifact is a one-way,
content-addressed snapshot of every step and component definition required by
that plan, plus the complete input/output variable data-flow graph.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.step_registry import StepRegistry
from automation_harness.core.test_plan import TestPlanError, _collect_refs, validate_plan, validate_plan_components
from automation_harness.models.plan import PlanVariableRef, TestPlan
from automation_harness.core.compound_steps import CompoundStepRepository, expand_compound_steps


COMPILED_TEST_FORMAT = "automation-harness/compiled-test-v1"


@dataclass(frozen=True)
class CompiledTest:
    document: Mapping[str, Any]

    @classmethod
    def from_document(cls, value: Any) -> "CompiledTest":
        if not isinstance(value, Mapping):
            raise TestPlanError("compiled test root must be a mapping")
        if value.get("format") != COMPILED_TEST_FORMAT:
            raise TestPlanError(f"unsupported compiled test format {value.get('format')!r}")
        artifact = value.get("artifact")
        if not isinstance(artifact, Mapping) or not isinstance(artifact.get("sha256"), str):
            raise TestPlanError("compiled test requires artifact.sha256")
        unsigned = dict(value)
        unsigned.pop("artifact", None)
        actual = _digest(_canonical(unsigned))
        if artifact["sha256"] != actual:
            raise TestPlanError(
                f"compiled test digest mismatch: expected {artifact['sha256']}, calculated {actual}"
            )
        instructions = value.get("instructions")
        dependencies = value.get("dependencies")
        if not isinstance(instructions, list) or not isinstance(dependencies, Mapping):
            raise TestPlanError("compiled test requires instructions and dependencies")
        return cls(dict(value))

    @property
    def digest(self) -> str:
        return str(self.document["artifact"]["sha256"])

    def to_dict(self) -> dict[str, Any]:
        return dict(self.document)

    def to_json(self) -> str:
        return json.dumps(self.document, indent=2, sort_keys=True) + "\n"

    def validate_runtime(self, registry: StepRegistry) -> list[str]:
        """Verify installed executable implementations match the compiled snapshot."""
        issues: list[str] = []
        steps = self.document["dependencies"].get("steps", {})
        if not isinstance(steps, Mapping):
            return ["compiled step dependency manifest must be a mapping"]
        for step_id, dependency in steps.items():
            try:
                installed = registry.get(str(step_id))
            except ValueError as exc:
                issues.append(str(exc))
                continue
            expected = dependency.get("implementation_sha256") if isinstance(dependency, Mapping) else None
            if installed.implementation_digest != expected:
                issues.append(
                    f"step {step_id!r} implementation mismatch: compiled={expected}, "
                    f"installed={installed.implementation_digest}"
                )
        return issues

    def component_repository(self) -> ComponentRepository:
        components = self.document["dependencies"].get("components", {})
        return ComponentRepository.from_document({"version": 2, "components": components}, source="compiled artifact")

    def runtime_plan(self) -> TestPlan:
        """Build runtime IR from compiled instructions, not semantic source."""
        source = self.document.get("source", {})
        variables = self.document.get("variables", {}).get("initial", {})
        calls: list[Any] = []
        from automation_harness.models.plan import StepCall
        for item in self.document["instructions"]:
            if not isinstance(item, Mapping):
                raise TestPlanError("compiled instruction must be a mapping")
            calls.append(StepCall(
                node_id=str(item["invocation_id"]),
                step_id=str(item["step"]),
                inputs=_decode(item.get("inputs", {})),
                outputs={str(key): str(value) for key, value in item.get("outputs", {}).items()},
                depends_on=tuple(str(value) for value in item.get("depends_on", [])),
                description=str(item.get("provenance", {}).get("description", "")),
                completion=_decode(item.get("completion", {"mode": "automatic"})),
                scope=_decode(item.get("scope", {})),
            ))
        return TestPlan(
            name=str(source.get("name", "compiled-test")),
            version=int(source.get("version", 1)),
            variables=_decode(variables),
            steps=tuple(calls),
        )


def load_compiled_test(path: Path) -> CompiledTest:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TestPlanError(f"cannot load compiled test {path}: {exc}") from exc
    return CompiledTest.from_document(raw)


def compile_test(
    plan: TestPlan,
    registry: StepRegistry,
    components: ComponentRepository,
    *,
    compiler_version: str = "1",
    compound_steps: CompoundStepRepository | None = None,
) -> CompiledTest:
    """Resolve a semantic plan into an immutable, repository-free artifact."""
    expanded_plan = plan
    used_compound_steps: tuple[str, ...] = ()
    if compound_steps is not None:
        expanded_plan, used_compound_steps = expand_compound_steps(plan, compound_steps)
    issues = validate_plan(expanded_plan, registry)
    issues.extend(validate_plan_components(expanded_plan, components))
    if issues:
        raise TestPlanError("test compilation failed:\n- " + "\n- ".join(issues))

    producers: dict[str, dict[str, str]] = {}
    consumers: dict[str, list[dict[str, str]]] = {}
    instructions: list[dict[str, Any]] = []
    referenced_components: set[str] = set()
    step_dependencies: dict[str, Mapping[str, Any]] = {}

    for index, call in enumerate(expanded_plan.steps):
        definition = registry.get(call.step_id)
        step_dependencies[definition.name] = definition.to_dict()
        for output_name, variable_name in sorted(call.outputs.items()):
            producers[variable_name] = {
                "invocation_id": call.node_id,
                "output": output_name,
            }
        refs = sorted(_collect_refs(call.inputs) | _collect_refs(call.scope) | _collect_refs(call.completion))
        for path in refs:
            consumers.setdefault(path.split(".", 1)[0], []).append(
                {"invocation_id": call.node_id, "path": path}
            )
        component_id = call.inputs.get("component_id")
        if isinstance(component_id, str) and components.contains(component_id):
            referenced_components.add(component_id)
        instructions.append({
            "index": index,
            "invocation_id": call.node_id,
            "step": definition.name,
            "implementation_sha256": definition.implementation_digest,
            "inputs": _encode(call.inputs),
            "outputs": dict(sorted(call.outputs.items())),
            "depends_on": sorted(call.depends_on),
            "scope": _encode(call.scope),
            "completion": _encode(call.completion),
            "provenance": {"semantic_step_id": call.node_id, "description": call.description},
        })

    component_dependencies = {
        component_id: _component_document(components, component_id)
        for component_id in sorted(referenced_components)
    }
    source = _canonical(plan.to_dict())
    dependency_manifest = {
        "steps": {key: step_dependencies[key] for key in sorted(step_dependencies)},
        "components": component_dependencies,
        "compound_steps": {
            step_id: compound_steps.get(step_id).to_dict()
            for step_id in used_compound_steps
        } if compound_steps is not None else {},
    }
    body: dict[str, Any] = {
        "format": COMPILED_TEST_FORMAT,
        "compiler_version": compiler_version,
        "source": {
            "name": plan.name,
            "version": plan.version,
            "sha256": _digest(source),
        },
        "dependencies": dependency_manifest,
        "variables": {
            "initial": _encode(plan.variables),
            "producers": producers,
            "consumers": {key: value for key, value in sorted(consumers.items())},
        },
        "instructions": instructions,
    }
    body["artifact"] = {"sha256": _digest(_canonical(body))}
    return CompiledTest(body)


def _component_document(repository: ComponentRepository, component_id: str) -> Mapping[str, Any]:
    return repository.to_document()["components"][component_id]


def _encode(value: Any) -> Any:
    if isinstance(value, PlanVariableRef):
        return {"$var": value.path}
    if isinstance(value, Mapping):
        return {str(key): _encode(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [_encode(item) for item in value]
    return value


def _decode(value: Any) -> Any:
    if isinstance(value, Mapping):
        if set(value) == {"$var"} and isinstance(value["$var"], str):
            return PlanVariableRef(value["$var"])
        return {str(key): _decode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode(item) for item in value]
    return value


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
