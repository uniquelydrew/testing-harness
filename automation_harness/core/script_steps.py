from __future__ import annotations

import hashlib
import inspect
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from automation_harness.core.step_registry import StepRegistryError, default_step_registry, step

_PROTOCOL_VERSION = 1
_MISSING = object()
_SCRIPT_STEPS: dict[str, "ScriptStepDefinition"] = {}


class ScriptStepError(ValueError):
    pass


class ScriptStepExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ScriptValueDefinition:
    name: str
    type_name: str = "Any"
    required: bool = True
    default: Any = _MISSING
    description: str = ""


@dataclass(frozen=True)
class ScriptStepDefinition:
    step_id: str
    manifest_path: Path
    script_path: Path
    interpreter: str
    description: str
    domain: str
    capabilities: frozenset[str]
    risk: str
    timeout: float | None
    inputs: tuple[ScriptValueDefinition, ...]
    outputs: tuple[ScriptValueDefinition, ...]
    script_sha256: str

    @classmethod
    def load(cls, path: str | Path) -> "ScriptStepDefinition":
        manifest_path = Path(path).expanduser().resolve()
        if not manifest_path.is_file():
            raise ScriptStepError(f"script-step manifest does not exist: {manifest_path}")
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, Mapping):
            raise ScriptStepError("script-step manifest root must be a mapping")
        if raw.get("version", 1) != 1:
            raise ScriptStepError(f"unsupported script-step version {raw.get('version')!r}")

        step_id = raw.get("id")
        if not isinstance(step_id, str) or not step_id.strip():
            raise ScriptStepError("script-step manifest requires a non-empty string id")
        step_id = step_id.strip()

        implementation = raw.get("implementation")
        if not isinstance(implementation, Mapping) or implementation.get("kind") != "script":
            raise ScriptStepError("script-step implementation.kind must be 'script'")
        script_value = implementation.get("path")
        if not isinstance(script_value, str) or not script_value.strip():
            raise ScriptStepError("script-step implementation requires a script path")
        script_path = (manifest_path.parent / script_value).resolve()
        if not script_path.is_file():
            raise ScriptStepError(f"script implementation does not exist: {script_path}")

        interpreter = implementation.get("interpreter", "direct")
        if not isinstance(interpreter, str) or not interpreter.strip():
            raise ScriptStepError("script-step interpreter must be a non-empty string")
        interpreter = interpreter.strip()

        timeout_value = implementation.get("timeout")
        timeout: float | None = None
        if timeout_value is not None:
            if not isinstance(timeout_value, (int, float)) or isinstance(timeout_value, bool) or timeout_value <= 0:
                raise ScriptStepError("script-step timeout must be a positive number")
            timeout = float(timeout_value)

        inputs = _load_values(raw.get("inputs", {}), section="inputs", default_required=True)
        outputs = _load_values(raw.get("outputs", {}), section="outputs", default_required=True, allow_defaults=False)
        risk = str(raw.get("risk", "application_control"))
        capabilities_raw = raw.get("capabilities", [])
        if not isinstance(capabilities_raw, list) or not all(isinstance(item, str) for item in capabilities_raw):
            raise ScriptStepError("script-step capabilities must be a list of strings")
        domain = raw.get("domain") or step_id.partition(".")[0]
        if not isinstance(domain, str) or not domain.strip():
            raise ScriptStepError("script-step domain must be a non-empty string")

        return cls(
            step_id=step_id,
            manifest_path=manifest_path,
            script_path=script_path,
            interpreter=interpreter,
            description=str(raw.get("description", "")).strip(),
            domain=domain.strip(),
            capabilities=frozenset(capabilities_raw),
            risk=risk,
            timeout=timeout,
            inputs=inputs,
            outputs=outputs,
            script_sha256=_sha256(script_path),
        )

    def command(self) -> list[str]:
        if self.interpreter == "python":
            return [sys.executable, str(self.script_path)]
        if self.interpreter == "sh":
            return ["/bin/sh", str(self.script_path)]
        if self.interpreter == "bash":
            return ["/bin/bash", str(self.script_path)]
        if self.interpreter == "direct":
            return [str(self.script_path)]
        return [self.interpreter, str(self.script_path)]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.step_id,
            "implementation": {
                "kind": "script",
                "path": str(self.script_path),
                "interpreter": self.interpreter,
                "sha256": self.script_sha256,
                "timeout": self.timeout,
            },
            "inputs": [_value_to_dict(item) for item in self.inputs],
            "outputs": [_value_to_dict(item) for item in self.outputs],
            "risk": self.risk,
            "capabilities": sorted(self.capabilities),
        }


def register_script_step(path: str | Path):
    definition = ScriptStepDefinition.load(path)
    existing = _SCRIPT_STEPS.get(definition.step_id)
    if existing is not None:
        if existing == definition:
            return default_step_registry().get(definition.step_id)
        raise ScriptStepError(
            f"script step {definition.step_id!r} is already registered from {existing.manifest_path}"
        )

    registry = default_step_registry()
    if registry.contains(definition.step_id):
        raise StepRegistryError(f"step {definition.step_id!r} is already registered")

    invoke = _script_callable(definition)
    invoke.__signature__ = _signature(definition)  # type: ignore[attr-defined]

    wrapped = step(
        definition.step_id,
        domain=definition.domain,
        description=definition.description,
        capabilities=definition.capabilities,
        risk=definition.risk,
        outputs={item.name: item.name for item in definition.outputs},
    )(invoke)
    _SCRIPT_STEPS[definition.step_id] = definition
    return wrapped.__automation_step__


def load_script_steps(paths) -> tuple[ScriptStepDefinition, ...]:
    loaded: list[ScriptStepDefinition] = []
    for path in paths:
        registered = register_script_step(path)
        definition = _SCRIPT_STEPS[registered.name]
        loaded.append(definition)
    return tuple(loaded)


def registered_script_step(step_id: str) -> ScriptStepDefinition:
    try:
        return _SCRIPT_STEPS[step_id]
    except KeyError as exc:
        raise ScriptStepError(f"script step {step_id!r} is not registered") from exc


def _script_callable(definition: ScriptStepDefinition):
    """Build a callable whose registry digest incorporates the script bytes.

    ``StepDefinition`` currently hashes Python implementation code. Generating
    this tiny adapter with the script digest as a code constant makes the
    existing qualification/catalog hash change whenever the external script
    changes, without weakening the normal step transaction machinery.
    """
    function_name = "script_" + definition.step_id.replace(".", "_").replace("-", "_")
    source = (
        "def %s(ctx, **kwargs):\n"
        "    implementation_sha256 = %r\n"
        "    return _runner(ctx, _definition, kwargs)\n"
    ) % (function_name, definition.script_sha256)
    namespace = {
        "__name__": __name__,
        "_runner": _execute_script_step,
        "_definition": definition,
    }
    code = compile(source, "<script-step:%s>" % definition.step_id, "exec")
    exec(code, namespace)
    invoke = namespace[function_name]
    invoke.__doc__ = definition.description
    return invoke


def _execute_script_step(ctx, definition: ScriptStepDefinition, supplied: Mapping[str, Any]) -> dict[str, Any]:
    inputs = _materialize_inputs(definition, supplied)
    envelope = {
        "protocol": _PROTOCOL_VERSION,
        "step": definition.step_id,
        "inputs": inputs,
    }
    ctx.evidence.record(
        "script_step_started",
        step=definition.step_id,
        manifest=str(definition.manifest_path),
        script=str(definition.script_path),
        script_sha256=definition.script_sha256,
        interpreter=definition.interpreter,
    )
    try:
        completed = subprocess.run(
            definition.command(),
            cwd=str(definition.manifest_path.parent),
            env=os.environ.copy(),
            input=json.dumps(envelope, sort_keys=True),
            text=True,
            capture_output=True,
            timeout=definition.timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ScriptStepExecutionError(
            f"script step {definition.step_id!r} exceeded timeout {definition.timeout}s"
        ) from exc
    except OSError as exc:
        raise ScriptStepExecutionError(
            f"script step {definition.step_id!r} could not start: {exc}"
        ) from exc

    if completed.returncode != 0:
        detail = completed.stderr.strip()
        suffix = f": {detail}" if detail else ""
        raise ScriptStepExecutionError(
            f"script step {definition.step_id!r} exited with {completed.returncode}{suffix}"
        )

    try:
        response = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ScriptStepExecutionError(
            f"script step {definition.step_id!r} did not return valid JSON"
        ) from exc
    if not isinstance(response, Mapping):
        raise ScriptStepExecutionError("script-step response must be a JSON object")
    if response.get("protocol") != _PROTOCOL_VERSION:
        raise ScriptStepExecutionError(
            f"script-step response protocol must be {_PROTOCOL_VERSION}"
        )
    outputs = response.get("outputs")
    if not isinstance(outputs, Mapping):
        raise ScriptStepExecutionError("script-step response requires an outputs object")
    validated = _validate_outputs(definition, outputs)
    ctx.evidence.record(
        "script_step_finished",
        step=definition.step_id,
        script_sha256=definition.script_sha256,
        outputs=validated,
        stderr=completed.stderr,
    )
    return validated


def _materialize_inputs(definition: ScriptStepDefinition, supplied: Mapping[str, Any]) -> dict[str, Any]:
    known = {item.name: item for item in definition.inputs}
    unexpected = sorted(set(supplied) - set(known))
    if unexpected:
        raise ScriptStepExecutionError(
            f"script step {definition.step_id!r} received undeclared inputs: {', '.join(unexpected)}"
        )
    result: dict[str, Any] = {}
    for item in definition.inputs:
        if item.name in supplied:
            value = supplied[item.name]
        elif item.default is not _MISSING:
            value = item.default
        elif item.required:
            raise ScriptStepExecutionError(
                f"script step {definition.step_id!r} is missing required input {item.name!r}"
            )
        else:
            continue
        if not _matches_type(value, item.type_name):
            raise ScriptStepExecutionError(
                f"script step {definition.step_id!r} input {item.name!r} expects {item.type_name}, "
                f"got {type(value).__name__}"
            )
        result[item.name] = value
    return result


def _validate_outputs(definition: ScriptStepDefinition, supplied: Mapping[str, Any]) -> dict[str, Any]:
    known = {item.name: item for item in definition.outputs}
    unexpected = sorted(set(supplied) - set(known))
    if unexpected:
        raise ScriptStepExecutionError(
            f"script step {definition.step_id!r} returned undeclared outputs: {', '.join(unexpected)}"
        )
    result: dict[str, Any] = {}
    for item in definition.outputs:
        if item.name not in supplied:
            raise ScriptStepExecutionError(
                f"script step {definition.step_id!r} did not return required output {item.name!r}"
            )
        value = supplied[item.name]
        if not _matches_type(value, item.type_name):
            raise ScriptStepExecutionError(
                f"script step {definition.step_id!r} output {item.name!r} expects {item.type_name}, "
                f"got {type(value).__name__}"
            )
        result[item.name] = value
    return result


def _signature(definition: ScriptStepDefinition) -> inspect.Signature:
    parameters = [
        inspect.Parameter("ctx", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation="TestContext")
    ]
    for item in definition.inputs:
        default = inspect.Parameter.empty
        if item.default is not _MISSING:
            default = item.default
        elif not item.required:
            default = None
        parameters.append(
            inspect.Parameter(
                item.name,
                inspect.Parameter.KEYWORD_ONLY,
                default=default,
                annotation=item.type_name,
            )
        )
    return inspect.Signature(parameters)


def _load_values(raw: Any, *, section: str, default_required: bool, allow_defaults: bool = True) -> tuple[ScriptValueDefinition, ...]:
    if not isinstance(raw, Mapping):
        raise ScriptStepError(f"script-step {section} must be a mapping")
    values: list[ScriptValueDefinition] = []
    for name, spec in raw.items():
        if not isinstance(name, str) or not name.isidentifier():
            raise ScriptStepError(f"script-step {section} names must be valid identifiers: {name!r}")
        if isinstance(spec, str):
            spec = {"type": spec}
        if not isinstance(spec, Mapping):
            raise ScriptStepError(f"script-step {section}.{name} must be a mapping or type string")
        type_name = spec.get("type", "Any")
        if not isinstance(type_name, str) or not type_name.strip():
            raise ScriptStepError(f"script-step {section}.{name}.type must be a non-empty string")
        required = spec.get("required", default_required)
        if not isinstance(required, bool):
            raise ScriptStepError(f"script-step {section}.{name}.required must be boolean")
        if section == "outputs" and not required:
            raise ScriptStepError(
                f"script-step outputs.{name} cannot be optional; every declared step output must resolve"
            )
        default = spec.get("default", _MISSING)
        if default is not _MISSING and not allow_defaults:
            raise ScriptStepError(f"script-step {section}.{name} cannot declare a default")
        if default is not _MISSING:
            required = False
            if not _matches_type(default, type_name):
                raise ScriptStepError(
                    f"script-step {section}.{name} default does not match {type_name}"
                )
        values.append(
            ScriptValueDefinition(
                name=name,
                type_name=type_name.strip(),
                required=required,
                default=default,
                description=str(spec.get("description", "")),
            )
        )
    return tuple(values)


def _matches_type(value: Any, type_name: str) -> bool:
    normalized = type_name.replace("typing.", "").replace(" ", "")
    if normalized in {"Any", "object", ""}:
        return True
    if "|" in normalized:
        return any(_matches_type(value, part) for part in normalized.split("|"))
    if normalized.startswith("Optional[") and normalized.endswith("]"):
        return value is None or _matches_type(value, normalized[9:-1])
    if normalized in {"None", "NoneType", "null"}:
        return value is None
    if normalized in {"str", "string"}:
        return isinstance(value, str)
    if normalized in {"bool", "boolean"}:
        return isinstance(value, bool)
    if normalized in {"int", "integer"}:
        return isinstance(value, int) and not isinstance(value, bool)
    if normalized in {"float", "number"}:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if normalized.startswith(("list[", "List[", "Sequence[", "array[")) or normalized in {"list", "array"}:
        return isinstance(value, (list, tuple)) and not isinstance(value, (str, bytes))
    if normalized.startswith(("dict[", "Dict[", "Mapping[", "object[")) or normalized in {"dict", "mapping"}:
        return isinstance(value, Mapping)
    return True


def _value_to_dict(value: ScriptValueDefinition) -> dict[str, Any]:
    payload = {
        "name": value.name,
        "type": value.type_name,
        "required": value.required,
        "description": value.description,
    }
    if value.default is not _MISSING:
        payload["default"] = value.default
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
