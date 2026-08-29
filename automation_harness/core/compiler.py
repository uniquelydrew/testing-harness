"""Deterministic compilation of semantic TestPlans into execution artifacts.

The semantic plan remains authoritative.  A compiled artifact is a one-way,
content-addressed snapshot of every step and component definition required by
that plan, plus the complete input/output variable data-flow graph.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.step_registry import StepRegistry
from automation_harness.core.test_plan import TestPlanError, _collect_refs, validate_plan, validate_plan_components
from automation_harness.models.plan import PlanVariableRef, TestPlan


COMPILED_TEST_FORMAT = "automation-harness/compiled-test-v1"


@dataclass(frozen=True)
class CompiledTest:
    document: Mapping[str, Any]

    @property
    def digest(self) -> str:
        return str(self.document["artifact"]["sha256"])

    def to_dict(self) -> dict[str, Any]:
        return dict(self.document)

    def to_json(self) -> str:
        return json.dumps(self.document, indent=2, sort_keys=True) + "\n"


def compile_test(
    plan: TestPlan,
    registry: StepRegistry,
    components: ComponentRepository,
    *,
    compiler_version: str = "1",
) -> CompiledTest:
    """Resolve a semantic plan into an immutable, repository-free artifact."""
    issues = validate_plan(plan, registry)
    issues.extend(validate_plan_components(plan, components))
    if issues:
        raise TestPlanError("test compilation failed:\n- " + "\n- ".join(issues))

    producers: dict[str, dict[str, str]] = {}
    consumers: dict[str, list[dict[str, str]]] = {}
    instructions: list[dict[str, Any]] = []
    referenced_components: set[str] = set()
    step_dependencies: dict[str, Mapping[str, Any]] = {}

    for index, call in enumerate(plan.steps):
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


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
