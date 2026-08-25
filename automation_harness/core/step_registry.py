from __future__ import annotations

import hashlib
import importlib
import importlib.util
import inspect
import sys
from contextvars import ContextVar
from dataclasses import dataclass
from difflib import get_close_matches
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, TYPE_CHECKING

if TYPE_CHECKING:
    from automation_harness.core.test_context import TestContext


StepCallable = Callable[..., Any]
_RESERVED_INVOCATION_PARAMETERS = {"bind_outputs"}
_ALLOWED_STEP_RISKS = frozenset({"read_only", "synthetic_control", "application_control", "physical_control"})

_ACTIVE_OUTPUT_BINDINGS: ContextVar[Mapping[str, str] | None] = ContextVar("automation_step_output_bindings", default=None)
_ACTIVE_INVOCATION_CAPTURE: ContextVar[list["StepInvocationResult"] | None] = ContextVar("automation_step_invocation_capture", default=None)


class StepRegistryError(ValueError):
    pass


class StepNotFoundError(StepRegistryError):
    pass


class StepCapabilityError(RuntimeError):
    pass


class StepOutputError(StepRegistryError):
    pass


@dataclass(frozen=True)
class StepInputDefinition:
    name: str
    kind: str
    required: bool
    default: Any
    annotation: str

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "name": self.name,
            "kind": self.kind,
            "required": self.required,
            "annotation": self.annotation,
        }
        if not self.required:
            payload["default"] = self.default
        return payload


@dataclass(frozen=True)
class StepOutputDefinition:
    name: str
    selector: str
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "selector": self.selector,
            "description": self.description,
        }


@dataclass(frozen=True)
class StepInvocationResult:
    """Authoritative result of one registered-step invocation.

    The raw function result, extracted named outputs, and committed bindings are
    captured once inside the step transaction so outer execution layers never
    have to re-read output selectors.
    """

    result: Any
    outputs: Mapping[str, Any]
    bindings: Mapping[str, str]


@dataclass(frozen=True)
class StepDefinition:
    """Metadata and implementation for one reusable automation step."""

    name: str
    function: StepCallable
    description: str
    domain: str
    capabilities: frozenset[str]
    risk: str
    aliases: tuple[str, ...]
    signature: inspect.Signature
    inputs: tuple[StepInputDefinition, ...]
    outputs: tuple[StepOutputDefinition, ...]
    source_module: str
    source_name: str
    implementation_digest: str

    @property
    def invocation_signature(self) -> inspect.Signature:
        parameters = list(self.signature.parameters.values())
        if parameters and parameters[0].name in {"ctx", "context"}:
            parameters = parameters[1:]
        return self.signature.replace(parameters=parameters)

    @property
    def output_names(self) -> frozenset[str]:
        return frozenset(output.name for output in self.outputs)

    def extract_outputs(self, result: Any) -> dict[str, Any]:
        return {
            output.name: _select_output(result, output.selector, step_name=self.name, output_name=output.name)
            for output in self.outputs
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "domain": self.domain,
            "capabilities": sorted(self.capabilities),
            "risk": self.risk,
            "aliases": list(self.aliases),
            "signature": str(self.invocation_signature),
            "inputs": [item.to_dict() for item in self.inputs],
            "outputs": [item.to_dict() for item in self.outputs],
            "source": f"{self.source_module}:{self.source_name}",
            "implementation_sha256": self.implementation_digest,
        }


class StepRegistry:
    """Stable semantic catalog of reusable automation actions and assertions."""

    def __init__(self) -> None:
        self._canonical: dict[str, StepDefinition] = {}
        self._lookup: dict[str, StepDefinition] = {}

    def register(self, definition: StepDefinition) -> None:
        names = (definition.name, *definition.aliases)
        for name in names:
            existing = self._lookup.get(name)
            if existing is not None and existing is not definition:
                raise StepRegistryError(
                    f"step name or alias {name!r} is already registered by {existing.name!r}"
                )
        existing_canonical = self._canonical.get(definition.name)
        if existing_canonical is not None and existing_canonical is not definition:
            raise StepRegistryError(f"step {definition.name!r} is already registered")
        self._canonical[definition.name] = definition
        for name in names:
            self._lookup[name] = definition

    def get(self, name: str) -> StepDefinition:
        try:
            return self._lookup[name]
        except KeyError as exc:
            suggestions = self.suggest(name)
            suffix = f"; possible matches: {', '.join(suggestions)}" if suggestions else ""
            raise StepNotFoundError(f"unknown registered step {name!r}{suffix}") from exc

    def contains(self, name: str) -> bool:
        return name in self._lookup

    def suggest(self, name: str, *, limit: int = 5) -> list[str]:
        return get_close_matches(name, self._lookup.keys(), n=limit, cutoff=0.45)

    def definitions(self, *, domain: str | None = None) -> tuple[StepDefinition, ...]:
        values = self._canonical.values()
        if domain is not None:
            values = (definition for definition in values if definition.domain == domain)
        return tuple(sorted(values, key=lambda definition: definition.name))

    def invoke(
        self,
        context: "TestContext",
        name: str,
        /,
        *args: Any,
        bind_outputs: Mapping[str, str] | None = None,
        **kwargs: Any,
    ) -> Any:
        return self._invoke(
            context,
            name,
            *args,
            bind_outputs=bind_outputs,
            detailed=False,
            **kwargs,
        )

    def invoke_detailed(
        self,
        context: "TestContext",
        name: str,
        /,
        *args: Any,
        bind_outputs: Mapping[str, str] | None = None,
        **kwargs: Any,
    ) -> StepInvocationResult:
        result = self._invoke(
            context,
            name,
            *args,
            bind_outputs=bind_outputs,
            detailed=True,
            **kwargs,
        )
        assert isinstance(result, StepInvocationResult)
        return result

    def _invoke(
        self,
        context: "TestContext",
        name: str,
        /,
        *args: Any,
        bind_outputs: Mapping[str, str] | None,
        detailed: bool,
        **kwargs: Any,
    ) -> Any:
        definition = self.get(name)
        if context.globals is None:
            raise RuntimeError("test context has no global variable store")
        _validate_output_bindings(definition, bind_outputs)
        resolved_args = tuple(context.globals.resolve_value(value) for value in args)
        resolved_kwargs = {key: context.globals.resolve_value(value) for key, value in kwargs.items()}
        binding_token = _ACTIVE_OUTPUT_BINDINGS.set(bind_outputs)
        capture: list[StepInvocationResult] | None = [] if detailed else None
        capture_token = _ACTIVE_INVOCATION_CAPTURE.set(capture)
        try:
            raw = definition.function(context, *resolved_args, **resolved_kwargs)
            if not detailed:
                return raw
            if capture is None or len(capture) != 1:
                raise RuntimeError(
                    f"step {definition.name!r} did not publish exactly one invocation result"
                )
            return capture[0]
        finally:
            _ACTIVE_INVOCATION_CAPTURE.reset(capture_token)
            _ACTIVE_OUTPUT_BINDINGS.reset(binding_token)

    def domains(self) -> tuple[str, ...]:
        return tuple(sorted({definition.domain for definition in self._canonical.values()}))


_DEFAULT_REGISTRY = StepRegistry()
_BUILTINS_LOADED = False


def step(
    name: str,
    *,
    domain: str | None = None,
    description: str | None = None,
    capabilities: Iterable[str] = (),
    risk: str = "read_only",
    aliases: Iterable[str] = (),
    outputs: Mapping[str, str] | Iterable[str] = (),
) -> Callable[[StepCallable], StepCallable]:
    """Register one reusable step with UFT-style input/output metadata.

    Inputs are derived from the Python signature after the leading ``ctx``.
    Outputs are explicitly declared. A mapping maps output names to selectors in
    the returned value. ``"$"`` selects the full return value; dotted selectors
    traverse mappings, sequences, or object attributes. A string iterable is a
    shorthand for selectors with the same name.

    Direct imports remain supported; registered calls add deferred variable
    resolution and optional output-to-global bindings around the same function.
    """

    _validate_step_name(name)
    if risk not in _ALLOWED_STEP_RISKS:
        raise StepRegistryError(
            f"invalid step risk {risk!r}; expected one of: {', '.join(sorted(_ALLOWED_STEP_RISKS))}"
        )
    aliases_tuple = tuple(aliases)
    for alias in aliases_tuple:
        _validate_step_name(alias)
    output_definitions = _normalize_outputs(outputs)

    def decorator(function: StepCallable) -> StepCallable:
        signature = inspect.signature(function)
        parameters = list(signature.parameters.values())
        if not parameters or parameters[0].name not in {"ctx", "context"}:
            raise StepRegistryError(
                f"registered step {name!r} must take TestContext as its first parameter named ctx or context"
            )
        invocation_parameters = parameters[1:]
        reserved = sorted(parameter.name for parameter in invocation_parameters if parameter.name in _RESERVED_INVOCATION_PARAMETERS)
        if reserved:
            raise StepRegistryError(
                f"registered step {name!r} uses reserved invocation parameter(s): {', '.join(reserved)}"
            )
        resolved_domain = domain or name.partition(".")[0]
        resolved_description = (description or inspect.getdoc(function) or "").strip()
        required = frozenset(capabilities)
        input_definitions = tuple(_input_definition(parameter) for parameter in invocation_parameters)

        @wraps(function)
        def wrapped(context: "TestContext", *args: Any, **kwargs: Any) -> Any:
            missing = sorted(required - context.capabilities)
            if missing:
                raise StepCapabilityError(
                    f"step {name!r} requires unavailable capabilities: {', '.join(missing)}"
                )
            signature.bind(context, *args, **kwargs)
            context.evidence.record(
                "step_started",
                step=name,
                domain=resolved_domain,
                arguments=_safe_arguments(signature, context, args, kwargs),
            )
            try:
                result = function(context, *args, **kwargs)
                extracted = {
                    output.name: _select_output(result, output.selector, step_name=name, output_name=output.name)
                    for output in output_definitions
                }
                bindings = _ACTIVE_OUTPUT_BINDINGS.get()
                if bindings:
                    _bind_step_outputs(context, definition, extracted, bindings)
                invocation = StepInvocationResult(
                    result=result,
                    outputs=dict(extracted),
                    bindings=dict(bindings or {}),
                )
                capture = _ACTIVE_INVOCATION_CAPTURE.get()
                if capture is not None:
                    capture.append(invocation)
                context.evidence.record(
                    "step_finished",
                    step=name,
                    domain=resolved_domain,
                    result=result,
                    outputs=extracted,
                    bindings=dict(bindings or {}),
                )
                return result
            except Exception as exc:
                context.evidence.record(
                    "step_failed",
                    step=name,
                    domain=resolved_domain,
                    error=f"{type(exc).__name__}: {exc}",
                )
                raise

        definition = StepDefinition(
            name=name,
            function=wrapped,
            description=resolved_description,
            domain=resolved_domain,
            capabilities=required,
            risk=risk,
            aliases=aliases_tuple,
            signature=signature,
            inputs=input_definitions,
            outputs=output_definitions,
            source_module=function.__module__,
            source_name=function.__name__,
            implementation_digest=_implementation_digest(function),
        )
        setattr(wrapped, "__automation_step__", definition)
        _DEFAULT_REGISTRY.register(definition)
        return wrapped

    return decorator


def default_step_registry() -> StepRegistry:
    load_builtin_steps()
    return _DEFAULT_REGISTRY


def load_builtin_steps() -> StepRegistry:
    global _BUILTINS_LOADED
    if _BUILTINS_LOADED:
        return _DEFAULT_REGISTRY
    for module in (
        "automation_harness.steps.camera_steps",
        "automation_harness.steps.mosaic_steps",
        "automation_harness.steps.navigation_steps",
        "automation_harness.steps.gui_steps",
        "automation_harness.steps.threat_steps",
        "automation_harness.steps.track_steps",
        "automation_harness.steps.validation_steps",
    ):
        importlib.import_module(module)
    _BUILTINS_LOADED = True
    return _DEFAULT_REGISTRY


def load_step_library(path: str | Path) -> StepRegistry:
    """Load one explicitly associated Python step library into the registry."""
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise StepRegistryError(f"step library does not exist: {resolved}")
    module_key = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:16]
    module_name = f"automation_harness_user_steps_{module_key}"
    if module_name in sys.modules:
        return _DEFAULT_REGISTRY
    spec = importlib.util.spec_from_file_location(module_name, resolved)
    if spec is None or spec.loader is None:
        raise StepRegistryError(f"cannot load step library: {resolved}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return _DEFAULT_REGISTRY


def load_step_libraries(paths: Iterable[str | Path]) -> StepRegistry:
    load_builtin_steps()
    for path in paths:
        load_step_library(path)
    return _DEFAULT_REGISTRY


def registered_step(name: str) -> StepDefinition:
    return default_step_registry().get(name)


def invoke_step(
    context: "TestContext",
    name: str,
    /,
    *args: Any,
    bind_outputs: Mapping[str, str] | None = None,
    **kwargs: Any,
) -> Any:
    return default_step_registry().invoke(
        context,
        name,
        *args,
        bind_outputs=bind_outputs,
        **kwargs,
    )


def _validate_step_name(name: str) -> None:
    if not isinstance(name, str) or not name.strip() or name != name.strip():
        raise StepRegistryError("step names must be non-empty strings without surrounding whitespace")
    parts = name.split(".")
    if any(not part or not part.replace("_", "").replace("-", "").isalnum() for part in parts):
        raise StepRegistryError(
            f"invalid step name {name!r}; use dot-separated alphanumeric, '-' or '_' segments"
        )


def _normalize_outputs(outputs: Mapping[str, str] | Iterable[str]) -> tuple[StepOutputDefinition, ...]:
    if isinstance(outputs, Mapping):
        items = list(outputs.items())
    elif isinstance(outputs, str):
        raise StepRegistryError("step outputs must be a mapping or iterable of names, not a string")
    else:
        items = [(name, name) for name in outputs]
    definitions: list[StepOutputDefinition] = []
    seen: set[str] = set()
    for output_name, selector in items:
        if not isinstance(output_name, str) or not output_name.strip() or output_name != output_name.strip():
            raise StepRegistryError("step output names must be non-empty strings without surrounding whitespace")
        if output_name in seen:
            raise StepRegistryError(f"duplicate step output name {output_name!r}")
        if not isinstance(selector, str) or not selector.strip() or selector != selector.strip():
            raise StepRegistryError(f"step output {output_name!r} must have a non-empty selector")
        seen.add(output_name)
        definitions.append(StepOutputDefinition(output_name, selector))
    return tuple(definitions)


def _input_definition(parameter: inspect.Parameter) -> StepInputDefinition:
    required = parameter.default is inspect.Parameter.empty
    default = None if required else parameter.default
    annotation = "Any" if parameter.annotation is inspect.Parameter.empty else _annotation_name(parameter.annotation)
    return StepInputDefinition(
        name=parameter.name,
        kind=parameter.kind.name.lower(),
        required=required,
        default=default,
        annotation=annotation,
    )


def _annotation_name(annotation: Any) -> str:
    if isinstance(annotation, str):
        return annotation
    if hasattr(annotation, "__name__"):
        return annotation.__name__
    return str(annotation).replace("typing.", "")


def _select_output(result: Any, selector: str, *, step_name: str, output_name: str) -> Any:
    if selector == "$":
        return result
    current = result
    for segment in selector.split("."):
        if isinstance(current, Mapping):
            if segment not in current:
                raise StepOutputError(
                    f"step {step_name!r} output {output_name!r} selector {selector!r} "
                    f"cannot find mapping key {segment!r}"
                )
            current = current[segment]
            continue
        if isinstance(current, (list, tuple)):
            try:
                index = int(segment)
            except ValueError as exc:
                raise StepOutputError(
                    f"step {step_name!r} output {output_name!r} selector {selector!r} "
                    f"requires an integer sequence index at {segment!r}"
                ) from exc
            try:
                current = current[index]
            except IndexError as exc:
                raise StepOutputError(
                    f"step {step_name!r} output {output_name!r} selector {selector!r} index {index} is out of range"
                ) from exc
            continue
        try:
            current = getattr(current, segment)
        except AttributeError:
            raise StepOutputError(
                f"step {step_name!r} output {output_name!r} selector {selector!r} "
                f"cannot traverse {segment!r} on {type(current).__name__}"
            ) from None
        continue
    return current


def _validate_output_bindings(
    definition: StepDefinition,
    bindings: Mapping[str, str] | None,
) -> None:
    if bindings is None:
        return
    if not isinstance(bindings, Mapping):
        raise StepOutputError("bind_outputs must be a mapping of step output names to global variable names")
    available = definition.output_names
    from automation_harness.core.variables import _validate_name as _validate_variable_name

    for output_name, variable_name in bindings.items():
        if output_name not in available:
            suggestions = get_close_matches(output_name, available, n=3, cutoff=0.45)
            suffix = f"; possible matches: {', '.join(suggestions)}" if suggestions else ""
            raise StepOutputError(
                f"step {definition.name!r} does not declare output {output_name!r}{suffix}"
            )
        if not isinstance(variable_name, str):
            raise StepOutputError("bind_outputs values must be global variable names")
        try:
            _validate_variable_name(variable_name)
        except ValueError as exc:
            raise StepOutputError(f"invalid output binding target {variable_name!r}: {exc}") from exc


def _bind_step_outputs(
    context: "TestContext",
    definition: StepDefinition,
    extracted: Mapping[str, Any],
    bindings: Mapping[str, str],
) -> None:
    _validate_output_bindings(definition, bindings)
    if context.globals is None:
        raise RuntimeError("test context has no global variable store")
    staged = {variable_name: extracted[output_name] for output_name, variable_name in bindings.items()}
    committed = context.globals.set_many_atomic(staged)
    for output_name, variable_name in bindings.items():
        context.evidence.record(
            "step_output_bound",
            step=definition.name,
            output=output_name,
            variable=variable_name,
            value=committed[variable_name],
        )


def _implementation_digest(function: StepCallable) -> str:
    """Hash the actual authored implementation, not only its public contract."""
    try:
        source = inspect.getsource(function).encode("utf-8")
    except (OSError, TypeError):
        code = getattr(function, "__code__", None)
        if code is None:
            source = repr(function).encode("utf-8")
        else:
            source = repr((code.co_code, code.co_consts, code.co_names)).encode("utf-8")
    return hashlib.sha256(source).hexdigest()


def _safe_arguments(
    signature: inspect.Signature,
    context: "TestContext",
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    bound = signature.bind(context, *args, **kwargs)
    bound.apply_defaults()
    return {
        key: value
        for key, value in bound.arguments.items()
        if key not in {"ctx", "context"}
    }
