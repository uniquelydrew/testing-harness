"""Serializable compound-step repositories and compile-time expansion."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from automation_harness.models.plan import PlanVariableRef, StepCall, TestPlan


class CompoundStepError(ValueError):
    pass


@dataclass(frozen=True)
class CompoundInputRef:
    name: str


@dataclass(frozen=True)
class CompoundLocalRef:
    name: str


@dataclass(frozen=True)
class CompoundStepDefinition:
    step_id: str
    description: str
    inputs: tuple[str, ...]
    outputs: Mapping[str, str]
    calls: tuple[StepCall, ...]
    completion: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "inputs": list(self.inputs),
            "outputs": dict(self.outputs),
            "calls": [_call_to_dict(call) for call in self.calls],
            "completion": _encode_compound(self.completion),
        }


@dataclass(frozen=True)
class CompoundStepRepository:
    definitions: Mapping[str, CompoundStepDefinition]

    @classmethod
    def load(cls, paths: list[Path] | tuple[Path, ...]) -> "CompoundStepRepository":
        merged: dict[str, CompoundStepDefinition] = {}
        for path in paths:
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except (OSError, yaml.YAMLError) as exc:
                raise CompoundStepError(f"cannot load step repository {path}: {exc}") from exc
            parsed = cls.from_document(raw, source=str(path))
            overlap = set(merged) & set(parsed.definitions)
            if overlap:
                raise CompoundStepError(
                    f"duplicate compound step definitions: {', '.join(sorted(overlap))}"
                )
            merged.update(parsed.definitions)
        return cls(merged)

    @classmethod
    def from_document(cls, raw: Any, *, source: str = "step repository") -> "CompoundStepRepository":
        if not isinstance(raw, Mapping) or raw.get("version", 1) != 1:
            raise CompoundStepError(f"{source}: unsupported or invalid step repository")
        entries = raw.get("steps", {})
        if not isinstance(entries, Mapping):
            raise CompoundStepError(f"{source}: steps must be a mapping")
        return cls({
            str(step_id): _parse_definition(str(step_id), value, source)
            for step_id, value in entries.items()
        })

    def contains(self, step_id: str) -> bool:
        return step_id in self.definitions

    def get(self, step_id: str) -> CompoundStepDefinition:
        try:
            return self.definitions[step_id]
        except KeyError as exc:
            raise CompoundStepError(f"unknown compound step {step_id!r}") from exc

    def to_document(self) -> dict[str, Any]:
        return {
            "version": 1,
            "steps": {
                step_id: definition.to_dict()
                for step_id, definition in sorted(self.definitions.items())
            },
        }


def expand_compound_steps(
    plan: TestPlan,
    repository: CompoundStepRepository,
) -> tuple[TestPlan, tuple[str, ...]]:
    expanded: list[StepCall] = []
    terminal_nodes: dict[str, str] = {}
    used: set[str] = set()
    for call in plan.steps:
        group = _expand_call(call, repository, stack=(), used=used)
        expanded.extend(group)
        if group:
            terminal_nodes[call.node_id] = group[-1].node_id
    rewritten = tuple(
        replace_dependencies(call, terminal_nodes)
        for call in expanded
    )
    return (
        TestPlan(
            name=plan.name,
            version=plan.version,
            variables=plan.variables,
            steps=rewritten,
            step_repositories=plan.step_repositories,
        ),
        tuple(sorted(used)),
    )


def _expand_call(
    call: StepCall,
    repository: CompoundStepRepository,
    *,
    stack: tuple[str, ...],
    used: set[str],
) -> list[StepCall]:
    if not repository.contains(call.step_id):
        return [call]
    if call.step_id in stack:
        cycle = " -> ".join((*stack, call.step_id))
        raise CompoundStepError(f"compound step dependency cycle: {cycle}")
    definition = repository.get(call.step_id)
    used.add(call.step_id)
    unknown = set(call.inputs) - set(definition.inputs)
    missing = set(definition.inputs) - set(call.inputs)
    if unknown or missing:
        details = []
        if unknown:
            details.append(f"unknown inputs: {', '.join(sorted(unknown))}")
        if missing:
            details.append(f"missing inputs: {', '.join(sorted(missing))}")
        raise CompoundStepError(f"compound step {call.step_id!r} invocation {call.node_id!r}: {'; '.join(details)}")
    unknown_outputs = set(call.outputs) - set(definition.outputs)
    if unknown_outputs:
        raise CompoundStepError(
            f"compound step {call.step_id!r} has no outputs: {', '.join(sorted(unknown_outputs))}"
        )

    local_to_outer = {
        local_name: call.outputs[public_name]
        for public_name, local_name in definition.outputs.items()
        if public_name in call.outputs
    }
    result: list[StepCall] = []
    previous: str | None = None
    for inner in definition.calls:
        node_id = f"{call.node_id}/{inner.node_id}"
        outputs = {
            output: local_to_outer.get(variable, _local_name(call.node_id, variable))
            for output, variable in inner.outputs.items()
        }
        dependencies = tuple(f"{call.node_id}/{item}" for item in inner.depends_on)
        if not dependencies:
            dependencies = (previous,) if previous is not None else call.depends_on
        bound = StepCall(
            node_id=node_id,
            step_id=inner.step_id,
            inputs=_bind(inner.inputs, call.inputs, call.node_id, local_to_outer),
            outputs=outputs,
            depends_on=dependencies,
            description=inner.description or f"{call.step_id}:{inner.node_id}",
            completion=_bind(inner.completion, call.inputs, call.node_id, local_to_outer),
            scope=_bind(inner.scope, call.inputs, call.node_id, local_to_outer),
        )
        nested = _expand_call(bound, repository, stack=(*stack, call.step_id), used=used)
        result.extend(nested)
        if nested:
            previous = nested[-1].node_id
    effective_completion = call.completion
    if effective_completion.get("mode", "automatic") == "automatic":
        effective_completion = _bind(definition.completion, call.inputs, call.node_id, local_to_outer)
    if effective_completion.get("mode", "automatic") != "automatic":
        barrier_id = f"{call.node_id}/$complete"
        result.append(StepCall(
            node_id=barrier_id,
            step_id="framework.completion.barrier",
            depends_on=(previous,) if previous is not None else call.depends_on,
            description=f"completion barrier for {call.step_id}",
            completion=effective_completion,
            scope=call.scope,
        ))
    return result


def replace_dependencies(call: StepCall, terminal_nodes: Mapping[str, str]) -> StepCall:
    dependencies = tuple(terminal_nodes.get(item, item) for item in call.depends_on)
    if dependencies == call.depends_on:
        return call
    from dataclasses import replace
    return replace(call, depends_on=dependencies)


def _bind(value: Any, inputs: Mapping[str, Any], prefix: str, exported: Mapping[str, str]) -> Any:
    if isinstance(value, CompoundInputRef):
        return inputs[value.name]
    if isinstance(value, CompoundLocalRef):
        return PlanVariableRef(exported.get(value.name, _local_name(prefix, value.name)))
    if isinstance(value, Mapping):
        return {str(key): _bind(item, inputs, prefix, exported) for key, item in value.items()}
    if isinstance(value, list):
        return [_bind(item, inputs, prefix, exported) for item in value]
    return value


def _local_name(prefix: str, name: str) -> str:
    safe_prefix = prefix.replace("/", "__").replace("-", "_")
    return f"{safe_prefix}__{name}"


def _parse_definition(step_id: str, value: Any, source: str) -> CompoundStepDefinition:
    if not isinstance(value, Mapping):
        raise CompoundStepError(f"{source}: step {step_id!r} must be a mapping")
    inputs = value.get("inputs", [])
    outputs = value.get("outputs", {})
    calls = value.get("calls", [])
    if not isinstance(inputs, list) or not all(isinstance(item, str) and item for item in inputs):
        raise CompoundStepError(f"{source}: step {step_id!r}.inputs must be a list of names")
    if not isinstance(outputs, Mapping) or not all(isinstance(key, str) and isinstance(item, str) for key, item in outputs.items()):
        raise CompoundStepError(f"{source}: step {step_id!r}.outputs must map public names to local variables")
    if not isinstance(calls, list) or not calls:
        raise CompoundStepError(f"{source}: step {step_id!r}.calls must be a non-empty list")
    parsed_calls: list[StepCall] = []
    for index, raw in enumerate(calls, start=1):
        if not isinstance(raw, Mapping) or not isinstance(raw.get("step"), str):
            raise CompoundStepError(f"{source}: step {step_id!r}.calls[{index - 1}] is invalid")
        parsed_calls.append(StepCall(
            node_id=str(raw.get("id") or f"step-{index:03d}"),
            step_id=raw["step"],
            inputs=_decode_compound(raw.get("inputs", {})),
            outputs={str(key): str(item) for key, item in raw.get("outputs", {}).items()},
            depends_on=tuple(str(item) for item in raw.get("depends_on", [])),
            description=str(raw.get("description", "")),
            completion=_decode_compound(raw.get("completion", {"mode": "automatic"})),
            scope=_decode_compound(raw.get("scope", {})),
        ))
    return CompoundStepDefinition(
        step_id=step_id,
        description=str(value.get("description", "")),
        inputs=tuple(inputs),
        outputs={str(key): str(item) for key, item in outputs.items()},
        calls=tuple(parsed_calls),
        completion=_decode_compound(value.get("completion", {"mode": "automatic"})),
    )


def _decode_compound(value: Any) -> Any:
    if isinstance(value, Mapping):
        if set(value) == {"$input"} and isinstance(value["$input"], str):
            return CompoundInputRef(value["$input"])
        if set(value) == {"$local"} and isinstance(value["$local"], str):
            return CompoundLocalRef(value["$local"])
        return {str(key): _decode_compound(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode_compound(item) for item in value]
    return value


def _encode_compound(value: Any) -> Any:
    if isinstance(value, CompoundInputRef):
        return {"$input": value.name}
    if isinstance(value, CompoundLocalRef):
        return {"$local": value.name}
    if isinstance(value, Mapping):
        return {str(key): _encode_compound(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode_compound(item) for item in value]
    return value


def _call_to_dict(call: StepCall) -> dict[str, Any]:
    return {
        "id": call.node_id,
        "step": call.step_id,
        "description": call.description,
        "inputs": _encode_compound(call.inputs),
        "outputs": dict(call.outputs),
        "depends_on": list(call.depends_on),
        "completion": _encode_compound(call.completion),
        "scope": _encode_compound(call.scope),
    }
