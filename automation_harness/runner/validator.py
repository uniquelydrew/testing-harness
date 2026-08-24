from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from automation_harness.core.component_repository import ComponentRepository, ComponentRepositoryError
from automation_harness.core.step_registry import StepRegistry, default_step_registry
from automation_harness.runner.bundle import TestBundle


@dataclass(frozen=True)
class ValidationIssue:
    path: Path
    line: int | None
    message: str

    def __str__(self) -> str:
        location = str(self.path)
        if self.line is not None:
            location += f":{self.line}"
        return f"{location}: {self.message}"


@dataclass(frozen=True)
class DeclaredStep:
    name: str
    aliases: tuple[str, ...]
    capabilities: frozenset[str]
    outputs: frozenset[str]
    path: Path
    line: int | None


def validate_bundle(bundle: TestBundle, *, backend_capabilities: set[str] | None = None) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if backend_capabilities is not None:
        missing = sorted(bundle.requires - backend_capabilities)
        if missing:
            issues.append(
                ValidationIssue(
                    bundle.root / "manifest.yaml",
                    None,
                    "backend is missing required capabilities: " + ", ".join(missing),
                )
            )

    repository: ComponentRepository | None = None
    component_paths = [Path(__file__).resolve().parents[1] / "resources" / "components.yaml"]
    if bundle.components is not None:
        component_paths.append(bundle.components)
    try:
        repository = ComponentRepository.load(component_paths)
    except ComponentRepositoryError as exc:
        issues.append(ValidationIssue(bundle.components or component_paths[0], None, str(exc)))

    step_registry = default_step_registry()
    declared_steps, library_issues = _scan_step_libraries(bundle.step_libraries)
    issues.extend(library_issues)
    issues.extend(_validate_declared_step_collisions(declared_steps, step_registry))
    issues.extend(
        _validate_step_library_references(
            bundle.step_libraries,
            step_registry,
            declared_steps,
            backend_capabilities,
        )
    )

    for test in bundle.tests:
        try:
            source = test.read_text(encoding="utf-8")
        except OSError as exc:
            issues.append(ValidationIssue(test, None, f"cannot read test: {exc}"))
            continue
        try:
            tree = ast.parse(source, filename=str(test))
        except SyntaxError as exc:
            issues.append(ValidationIssue(test, exc.lineno, exc.msg))
            continue
        has_test = any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
            for node in ast.walk(tree)
        )
        if not has_test:
            issues.append(ValidationIssue(test, None, "no test_* function found"))
        if repository is not None:
            issues.extend(_validate_component_references(test, tree, repository))
        issues.extend(
            _validate_step_references(
                test,
                tree,
                step_registry,
                declared_steps,
                backend_capabilities,
            )
        )

    return issues



def _validate_step_library_references(
    paths: tuple[Path, ...],
    registry: StepRegistry,
    declared_steps: tuple[DeclaredStep, ...],
    backend_capabilities: set[str] | None,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for path in paths:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):
            # The primary library scan already reports these errors.
            continue
        issues.extend(_validate_step_references(path, tree, registry, declared_steps, backend_capabilities))
    return issues

def _validate_component_references(
    path: Path,
    tree: ast.AST,
    repository: ComponentRepository,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        component_arg = None
        if isinstance(node.func, ast.Attribute) and node.func.attr == "component" and node.args:
            component_arg = node.args[0]
        elif isinstance(node.func, ast.Name) and node.func.id in {"resolve_component", "activate_component"} and len(node.args) >= 2:
            component_arg = node.args[1]
        if component_arg is None or not isinstance(component_arg, ast.Constant) or not isinstance(component_arg.value, str):
            continue
        component_id = component_arg.value
        if repository.contains(component_id):
            continue
        suggestions = repository.suggest(component_id)
        message = f"unknown component reference {component_id!r}"
        if suggestions:
            message += "; possible matches: " + ", ".join(suggestions)
        issues.append(ValidationIssue(path, getattr(node, "lineno", None), message))
    return issues


def _scan_step_libraries(paths: tuple[Path, ...]) -> tuple[tuple[DeclaredStep, ...], list[ValidationIssue]]:
    declarations: list[DeclaredStep] = []
    issues: list[ValidationIssue] = []
    for path in paths:
        try:
            source = path.read_text(encoding="utf-8")
        except OSError as exc:
            issues.append(ValidationIssue(path, None, f"cannot read step library: {exc}"))
            continue
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            issues.append(ValidationIssue(path, exc.lineno, exc.msg))
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                declaration, error = _parse_step_decorator(path, decorator)
                if error is not None:
                    issues.append(error)
                elif declaration is not None:
                    declarations.append(declaration)
    return tuple(declarations), issues


def _parse_step_decorator(path: Path, decorator: ast.AST) -> tuple[DeclaredStep | None, ValidationIssue | None]:
    if not isinstance(decorator, ast.Call):
        return None, None
    target = decorator.func
    is_step = (isinstance(target, ast.Name) and target.id == "step") or (
        isinstance(target, ast.Attribute) and target.attr == "step"
    )
    if not is_step:
        return None, None
    line = getattr(decorator, "lineno", None)
    if not decorator.args or not isinstance(decorator.args[0], ast.Constant) or not isinstance(decorator.args[0].value, str):
        return None, ValidationIssue(path, line, "@step registration requires a literal string step name")
    name = decorator.args[0].value
    aliases: tuple[str, ...] = ()
    capabilities: frozenset[str] = frozenset()
    outputs: frozenset[str] = frozenset()
    for keyword in decorator.keywords:
        if keyword.arg == "aliases":
            parsed = _literal_string_collection(keyword.value)
            if parsed is None:
                return None, ValidationIssue(path, line, "@step aliases must be a literal list/tuple/set of strings")
            aliases = tuple(parsed)
        elif keyword.arg == "capabilities":
            parsed = _literal_string_collection(keyword.value)
            if parsed is None:
                return None, ValidationIssue(path, line, "@step capabilities must be a literal list/tuple/set of strings")
            capabilities = frozenset(parsed)
        elif keyword.arg == "outputs":
            parsed_outputs = _literal_output_names(keyword.value)
            if parsed_outputs is None:
                return None, ValidationIssue(path, line, "@step outputs must be a literal mapping or list/tuple/set of strings")
            outputs = frozenset(parsed_outputs)
    return DeclaredStep(name, aliases, capabilities, outputs, path, line), None


def _literal_string_collection(node: ast.AST) -> list[str] | None:
    if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return None
    values: list[str] = []
    for element in node.elts:
        if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
            return None
        values.append(element.value)
    return values


def _literal_output_names(node: ast.AST) -> list[str] | None:
    if isinstance(node, ast.Dict):
        names: list[str] = []
        for key, value in zip(node.keys, node.values):
            if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                return None
            if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                return None
            names.append(key.value)
        return names
    return _literal_string_collection(node)


def _literal_bind_outputs(node: ast.AST) -> dict[str, str] | None:
    if not isinstance(node, ast.Dict):
        return None
    result: dict[str, str] = {}
    for key, value in zip(node.keys, node.values):
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            return None
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            return None
        result[key.value] = value.value
    return result


def _validate_declared_step_collisions(
    declarations: tuple[DeclaredStep, ...],
    registry: StepRegistry,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    seen: dict[str, DeclaredStep] = {}
    for declaration in declarations:
        for name in (declaration.name, *declaration.aliases):
            if registry.contains(name):
                existing = registry.get(name)
                issues.append(
                    ValidationIssue(
                        declaration.path,
                        declaration.line,
                        f"registered step name or alias {name!r} collides with built-in step {existing.name!r}",
                    )
                )
                continue
            previous = seen.get(name)
            if previous is not None:
                issues.append(
                    ValidationIssue(
                        declaration.path,
                        declaration.line,
                        f"registered step name or alias {name!r} duplicates declaration in {previous.path}",
                    )
                )
                continue
            seen[name] = declaration
    return issues


def _validate_step_references(
    path: Path,
    tree: ast.AST,
    registry: StepRegistry,
    declared_steps: tuple[DeclaredStep, ...],
    backend_capabilities: set[str] | None,
) -> list[ValidationIssue]:
    """Validate literal ctx.run_step()/invoke_step() references before startup."""
    issues: list[ValidationIssue] = []
    declared_lookup: dict[str, DeclaredStep] = {}
    for declaration in declared_steps:
        for name in (declaration.name, *declaration.aliases):
            declared_lookup.setdefault(name, declaration)
    all_names = tuple(registry.get(definition.name).name for definition in registry.definitions()) + tuple(declared_lookup)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        step_arg: ast.AST | None = None
        if isinstance(node.func, ast.Attribute) and node.func.attr == "run_step" and node.args:
            step_arg = node.args[0]
        elif isinstance(node.func, ast.Name) and node.func.id == "invoke_step" and len(node.args) >= 2:
            step_arg = node.args[1]
        if not isinstance(step_arg, ast.Constant) or not isinstance(step_arg.value, str):
            continue
        step_name = step_arg.value
        definition = None
        declaration = None
        if registry.contains(step_name):
            definition = registry.get(step_name)
        else:
            declaration = declared_lookup.get(step_name)
        if definition is None and declaration is None:
            from difflib import get_close_matches

            suggestions = get_close_matches(step_name, all_names, n=5, cutoff=0.45)
            message = f"unknown registered step {step_name!r}"
            if suggestions:
                message += "; possible matches: " + ", ".join(suggestions)
            issues.append(ValidationIssue(path, getattr(node, "lineno", None), message))
            continue
        bind_keyword = next((keyword for keyword in node.keywords if keyword.arg == "bind_outputs"), None)
        if bind_keyword is not None:
            bindings = _literal_bind_outputs(bind_keyword.value)
            if bindings is None:
                issues.append(
                    ValidationIssue(
                        path,
                        getattr(node, "lineno", None),
                        "bind_outputs must be a literal mapping of output names to global variable names for static validation",
                    )
                )
            else:
                available_outputs = definition.output_names if definition is not None else declaration.outputs
                for output_name, variable_name in bindings.items():
                    if output_name not in available_outputs:
                        from difflib import get_close_matches

                        suggestions = get_close_matches(output_name, available_outputs, n=3, cutoff=0.45)
                        message = f"step {step_name!r} does not declare output {output_name!r}"
                        if suggestions:
                            message += "; possible matches: " + ", ".join(suggestions)
                        issues.append(ValidationIssue(path, getattr(node, "lineno", None), message))
                    if not variable_name or "." in variable_name:
                        issues.append(
                            ValidationIssue(
                                path,
                                getattr(node, "lineno", None),
                                f"bind_outputs target {variable_name!r} must be a non-empty top-level global variable name without '.'",
                            )
                        )

        if backend_capabilities is not None:
            required = definition.capabilities if definition is not None else declaration.capabilities
            missing = sorted(required - backend_capabilities)
            if missing:
                issues.append(
                    ValidationIssue(
                        path,
                        getattr(node, "lineno", None),
                        f"step {step_name!r} requires backend capabilities: {', '.join(missing)}",
                    )
                )
    return issues
