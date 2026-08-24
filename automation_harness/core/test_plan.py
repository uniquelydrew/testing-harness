from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

import yaml

from automation_harness.core.step_registry import StepRegistry
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.models.plan import ExecutionState, PlanVariableRef, StepCall, StepStatus, TestPlan


class TestPlanError(ValueError):
    __test__ = False



def load_plan(path: Path) -> TestPlan:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, Mapping):
        raise TestPlanError("test plan root must be a mapping")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise TestPlanError("test plan requires a non-empty name")
    version = raw.get("version", 1)
    if version != 1:
        raise TestPlanError(f"unsupported test plan version {version!r}")
    variables = raw.get("variables", {})
    if not isinstance(variables, Mapping):
        raise TestPlanError("test plan variables must be a mapping")
    steps_raw = raw.get("steps", [])
    if not isinstance(steps_raw, list):
        raise TestPlanError("test plan steps must be a list")
    steps: list[StepCall] = []
    for index, item in enumerate(steps_raw, start=1):
        if not isinstance(item, Mapping):
            raise TestPlanError(f"steps[{index - 1}] must be a mapping")
        node_id = str(item.get("id") or f"step-{index:03d}")
        step_id = item.get("step")
        if not isinstance(step_id, str) or not step_id:
            raise TestPlanError(f"step {node_id!r} requires a registered step id")
        inputs = item.get("inputs", {})
        outputs = item.get("outputs", {})
        depends_on = item.get("depends_on", [])
        if not isinstance(inputs, Mapping) or not isinstance(outputs, Mapping) or not isinstance(depends_on, list):
            raise TestPlanError(f"step {node_id!r} has invalid inputs/outputs/depends_on")
        steps.append(
            StepCall(
                node_id=node_id,
                step_id=step_id,
                inputs=_decode_refs(dict(inputs)),
                outputs={str(k): str(v) for k, v in outputs.items()},
                depends_on=tuple(str(value) for value in depends_on),
                description=str(item.get("description", "")),
            )
        )
    return TestPlan(name=name, version=1, variables=_decode_refs(dict(variables)), steps=tuple(steps))


def save_plan(plan: TestPlan, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(plan.to_dict(), sort_keys=False, allow_unicode=True), encoding="utf-8")


def validate_plan(plan: TestPlan, registry: StepRegistry) -> list[str]:
    """Validate a declarative plan without touching an execution backend."""
    issues: list[str] = []
    node_ids = [step.node_id for step in plan.steps]
    if len(set(node_ids)) != len(node_ids):
        issues.append("step node ids must be unique")
    known_nodes = set(node_ids)
    initial_variables = set(plan.variables)
    produced_by: dict[str, list[str]] = {}

    definitions: dict[str, Any] = {}
    for call in plan.steps:
        try:
            definition = registry.get(call.step_id)
            definitions[call.node_id] = definition
        except ValueError as exc:
            issues.append(f"{call.node_id}: {exc}")
            continue
        for output, variable in call.outputs.items():
            if output not in definition.output_names:
                issues.append(f"{call.node_id}: step {definition.name!r} has no output {output!r}")
            if not variable or "." in variable:
                issues.append(f"{call.node_id}: invalid output variable {variable!r}")
            else:
                produced_by.setdefault(variable, []).append(call.node_id)

    explicit_edges: dict[str, set[str]] = {node_id: set() for node_id in known_nodes}
    for call in plan.steps:
        definition = definitions.get(call.node_id)
        for dependency in call.depends_on:
            if dependency not in known_nodes:
                issues.append(f"{call.node_id}: unknown dependency {dependency!r}")
            elif dependency == call.node_id:
                issues.append(f"{call.node_id}: step cannot depend on itself")
            else:
                explicit_edges[dependency].add(call.node_id)
        if definition is None:
            continue
        declared_inputs = {item.name: item for item in definition.inputs}
        for supplied in call.inputs:
            if supplied not in declared_inputs:
                issues.append(f"{call.node_id}: step {definition.name!r} has no input {supplied!r}")
        for item in definition.inputs:
            if item.required and item.name not in call.inputs:
                issues.append(f"{call.node_id}: missing required input {item.name!r}")
                continue
            if item.name in call.inputs:
                value = call.inputs[item.name]
                if not _contains_variable_ref(value) and not _matches_input_annotation(value, item.annotation):
                    issues.append(
                        f"{call.node_id}: input {item.name!r} for step {definition.name!r} "
                        f"expects {item.annotation}, got {type(value).__name__}"
                    )

    # Variable references may consume an initial variable or an output produced
    # anywhere in the graph. A later producer is valid: the managed queue keeps
    # the consumer BLOCKED until the producer commits the value.
    data_edges: dict[str, set[str]] = {node_id: set() for node_id in known_nodes}
    for call in plan.steps:
        for path in _collect_refs(call.inputs):
            root = path.split(".", 1)[0]
            if _path_available(plan.variables, path):
                continue
            producers = produced_by.get(root, [])
            if not producers:
                if root in initial_variables:
                    issues.append(
                        f"{call.node_id}: input references missing nested variable path {path!r} "
                        f"inside initial variable {root!r}"
                    )
                else:
                    issues.append(f"{call.node_id}: input references variable {path!r} with no default or producer")
                continue
            if len(producers) > 1:
                issues.append(
                    f"{call.node_id}: input variable {root!r} has ambiguous producers: {', '.join(producers)}"
                )
                continue
            producer = producers[0]
            if producer == call.node_id:
                issues.append(f"{call.node_id}: input variable {root!r} is produced by the same step")
            else:
                data_edges[producer].add(call.node_id)

    graph = {node_id: set(explicit_edges[node_id]) | set(data_edges[node_id]) for node_id in known_nodes}
    cycle = _find_cycle(graph)
    if cycle:
        issues.append("execution dependency cycle: " + " -> ".join(cycle))
    return issues


def validate_plan_components(plan: TestPlan, repository: ComponentRepository) -> list[str]:
    """Validate literal component references against the repository used for execution.

    Variable-driven component IDs remain runtime-resolved, but literal IDs can and
    should be rejected before any backend is started.
    """
    issues: list[str] = []
    for call in plan.steps:
        component_id = call.inputs.get("component_id")
        if not isinstance(component_id, str):
            continue
        if not repository.contains(component_id):
            suggestions = repository.suggest(component_id)
            suffix = f"; possible matches: {', '.join(suggestions)}" if suggestions else ""
            issues.append(f"{call.node_id}: unknown component {component_id!r}{suffix}")
            continue
        if call.step_id == "navigation.component.activate":
            definition = repository.get(component_id)
            if "activate" not in definition.actions:
                issues.append(f"{call.node_id}: component {component_id!r} does not support activation")
    return issues


def validate_plan_execution(
    plan: TestPlan,
    registry: StepRegistry,
    *,
    backend_capabilities: set[str] | frozenset[str],
    allowed_step_risks: set[str] | frozenset[str],
) -> list[str]:
    """Validate backend-specific capability and risk authorization preflight."""
    issues: list[str] = []
    available = set(backend_capabilities)
    allowed = set(allowed_step_risks)
    for call in plan.steps:
        try:
            definition = registry.get(call.step_id)
        except ValueError:
            continue
        missing = sorted(definition.capabilities - available)
        if missing:
            issues.append(
                f"{call.node_id}: step {definition.name!r} requires backend capabilities: {', '.join(missing)}"
            )
        if definition.risk not in allowed:
            issues.append(
                f"{call.node_id}: step {definition.name!r} risk {definition.risk!r} is not authorized by backend"
            )
    return issues


def _find_cycle(graph: Mapping[str, set[str]]) -> tuple[str, ...]:
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []

    def visit(node: str) -> tuple[str, ...]:
        if node in visiting:
            index = stack.index(node)
            return tuple((*stack[index:], node))
        if node in visited:
            return ()
        visiting.add(node)
        stack.append(node)
        for child in sorted(graph.get(node, ())):
            cycle = visit(child)
            if cycle:
                return cycle
        stack.pop()
        visiting.remove(node)
        visited.add(node)
        return ()

    for node in sorted(graph):
        cycle = visit(node)
        if cycle:
            return cycle
    return ()


def derive_execution_state(plan: TestPlan) -> ExecutionState:
    state = ExecutionState.from_plan(plan)
    completed: set[str] = set()
    # Initial queue state only. Runtime executor will reevaluate after each commit.
    for call in plan.steps:
        refs = tuple(sorted(_collect_refs(call.inputs)))
        unresolved = tuple(path for path in refs if not _path_available(plan.variables, path))
        dependency_block = tuple(dep for dep in call.depends_on if dep not in completed)
        node = state.steps[call.node_id]
        node.unresolved_variables = unresolved
        if unresolved or dependency_block:
            node.status = StepStatus.BLOCKED
        else:
            node.status = StepStatus.READY
        # Outputs are not available until the producing node actually commits.
    return state


def plan_to_json(plan: TestPlan) -> str:
    return json.dumps(plan.to_dict(), indent=2, default=str)


def _decode_refs(value: Any) -> Any:
    if isinstance(value, Mapping):
        if set(value) == {"$var"} and isinstance(value["$var"], str):
            return PlanVariableRef(value["$var"])
        return {str(key): _decode_refs(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode_refs(item) for item in value]
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        return PlanVariableRef(value[2:-1])
    return value


def _contains_variable_ref(value: Any) -> bool:
    if isinstance(value, PlanVariableRef):
        return True
    if isinstance(value, Mapping):
        return any(_contains_variable_ref(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_variable_ref(item) for item in value)
    return False


def _matches_input_annotation(value: Any, annotation: str) -> bool:
    normalized = annotation.replace("typing.", "").replace(" ", "")
    if normalized in {"Any", "object", ""}:
        return True
    if "|" in normalized:
        return any(_matches_input_annotation(value, part) for part in normalized.split("|"))
    if normalized.startswith("Optional[") and normalized.endswith("]"):
        return value is None or _matches_input_annotation(value, normalized[9:-1])
    if normalized in {"None", "NoneType"}:
        return value is None
    if normalized == "str":
        return isinstance(value, str)
    if normalized == "bool":
        return isinstance(value, bool)
    if normalized == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if normalized == "float":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if normalized.startswith(("Sequence[", "list[", "List[")):
        return isinstance(value, (list, tuple)) and not isinstance(value, (str, bytes))
    if normalized.startswith(("Mapping[", "dict[", "Dict[")):
        return isinstance(value, Mapping)
    if normalized.startswith(("tuple[", "Tuple[")):
        return isinstance(value, tuple)
    # Unknown/project-specific annotations remain runtime-validated rather than
    # causing false preflight failures.
    return True


def _collect_refs(value: Any) -> set[str]:
    result: set[str] = set()
    if isinstance(value, PlanVariableRef):
        result.add(value.path)
    elif isinstance(value, Mapping):
        for item in value.values():
            result.update(_collect_refs(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            result.update(_collect_refs(item))
    return result


def _path_available(values: Mapping[str, Any], path: str) -> bool:
    current: Any = values
    for segment in path.split("."):
        if isinstance(current, Mapping):
            if segment not in current:
                return False
            current = current[segment]
            continue
        if isinstance(current, (list, tuple)):
            try:
                index = int(segment)
                current = current[index]
            except (ValueError, IndexError):
                return False
            continue
        try:
            current = getattr(current, segment)
        except AttributeError:
            return False
    return True


class ManagedExecutionQueue:
    """Mutable run state derived from an immutable TestPlan.

    The queue is a derived view; the authoritative state remains the plan plus
    committed variables and per-node statuses. Recompute may therefore rebuild
    READY/BLOCKED state at any time without losing execution history.
    """

    def __init__(self, plan: TestPlan) -> None:
        self.plan = plan
        self.state = ExecutionState.from_plan(plan)
        self.state.variables = dict(plan.variables)
        self.recompute()

    def ready(self) -> tuple[str, ...]:
        return tuple(
            call.node_id
            for call in self.plan.steps
            if self.state.steps[call.node_id].status is StepStatus.READY
        )

    def start(self, node_id: str, resolved_inputs: Mapping[str, Any] | None = None) -> None:
        node = self._node(node_id)
        if node.status is not StepStatus.READY:
            raise TestPlanError(f"step {node_id!r} is {node.status.value}, not ready")
        node.status = StepStatus.RUNNING
        node.attempts += 1
        node.resolved_inputs = dict(resolved_inputs or {})

    def complete(self, node_id: str, outputs: Mapping[str, Any]) -> None:
        node = self._node(node_id)
        if node.status is not StepStatus.RUNNING:
            raise TestPlanError(f"step {node_id!r} is {node.status.value}, not running")
        call = self._call(node_id)
        unexpected = set(outputs) - set(call.outputs)
        if unexpected:
            raise TestPlanError(f"step {node_id!r} returned unbound outputs: {', '.join(sorted(unexpected))}")
        staged = dict(self.state.variables)
        for output_name, value in outputs.items():
            staged[call.outputs[output_name]] = value
        self.state.variables = staged
        node.outputs = dict(outputs)
        node.status = StepStatus.PASSED
        node.unresolved_variables = ()
        self.recompute()

    def fail(self, node_id: str, error: str) -> None:
        node = self._node(node_id)
        if node.status is not StepStatus.RUNNING:
            raise TestPlanError(f"step {node_id!r} is {node.status.value}, not running")
        node.status = StepStatus.FAILED
        node.error = error
        self.recompute()

    def recompute(self) -> None:
        terminal = {StepStatus.PASSED, StepStatus.FAILED, StepStatus.SKIPPED, StepStatus.CANCELLED, StepStatus.RUNNING}
        for call in self.plan.steps:
            node = self.state.steps[call.node_id]
            if node.status in terminal:
                continue
            refs = tuple(sorted(_collect_refs(call.inputs)))
            unresolved = tuple(path for path in refs if not _path_available(self.state.variables, path))
            unmet_dependencies = tuple(
                dep for dep in call.depends_on
                if self.state.steps.get(dep) is None or self.state.steps[dep].status is not StepStatus.PASSED
            )
            node.unresolved_variables = unresolved
            node.status = StepStatus.BLOCKED if unresolved or unmet_dependencies else StepStatus.READY

    def _node(self, node_id: str):
        try:
            return self.state.steps[node_id]
        except KeyError as exc:
            raise TestPlanError(f"unknown plan node {node_id!r}") from exc

    def _call(self, node_id: str) -> StepCall:
        for call in self.plan.steps:
            if call.node_id == node_id:
                return call
        raise TestPlanError(f"unknown plan node {node_id!r}")
