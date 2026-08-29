from __future__ import annotations

import hashlib
import json
import platform
from pathlib import Path
from typing import Any, Mapping, TYPE_CHECKING

if TYPE_CHECKING:
    from automation_harness.core.compiler import CompiledTest

from automation_harness.backends.base import ExecutionBackend
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.completion import await_step_completion
from automation_harness.core.step_registry import default_step_registry
from automation_harness.core.test_context import TestContext
from automation_harness.core.test_plan import ManagedExecutionQueue, validate_plan, validate_plan_components, validate_plan_execution
from automation_harness.core.variables import VariableRef, VariableStore
from automation_harness.models.plan import PlanVariableRef, StepStatus, TestPlan
from automation_harness.models.run import RunResult, utc_now
from automation_harness.reference.protocol import ReferenceClient
from automation_harness.reporting.artifacts import RunArtifacts


def execute_plan(
    plan: TestPlan,
    backend: ExecutionBackend,
    *,
    runs_dir: Path,
    variable_overrides: Mapping[str, object] | None = None,
    component_repository: ComponentRepository | None = None,
    compiled_artifact: "CompiledTest | None" = None,
) -> RunResult:
    """Execute a validated declarative TestPlan without importing test code.

    Only the installed built-in step catalog is available. No bundle-local Python
    step libraries are imported, making this the execution path intended to
    evolve into protected/production mode.
    """

    artifacts = RunArtifacts.create(runs_dir, plan.name)
    result = RunResult(
        run_id=artifacts.root.name,
        backend=backend.name,
        bundle=plan.name,
        started_at=utc_now(),
        artifact_dir=artifacts.root,
    )
    recorder = artifacts.recorder()
    recorder.record(
        "plan_run_started",
        plan=plan.name,
        backend=backend.name,
        compiled_artifact_sha256=compiled_artifact.digest if compiled_artifact else None,
    )

    registry = default_step_registry()
    initial_variables = dict(plan.variables)
    if variable_overrides:
        initial_variables.update(variable_overrides)
    runtime_plan = TestPlan(
        name=plan.name,
        version=plan.version,
        variables=initial_variables,
        steps=plan.steps,
        step_repositories=plan.step_repositories,
    )

    package_components = Path(__file__).resolve().parents[1] / "resources" / "components.yaml"
    package_repository = ComponentRepository.load([package_components])
    components = package_repository if component_repository is None else package_repository.overlay(component_repository)

    issues = validate_plan(runtime_plan, registry)
    issues.extend(validate_plan_components(runtime_plan, components))
    issues.extend(
        validate_plan_execution(
            runtime_plan,
            registry,
            backend_capabilities=backend.capabilities,
            allowed_step_risks=backend.allowed_step_risks,
        )
    )
    preflight = backend.preflight_issues()
    if issues or preflight:
        result.validation_errors = [*issues, *[f"backend preflight: {item}" for item in preflight]]
        result.exit_code = 2
        recorder.record("plan_validation_failed", issues=result.validation_errors)
        return _finalize(runtime_plan, backend, result, artifacts, recorder, initial_variables, registry=registry, compiled_artifact=compiled_artifact)

    queue = ManagedExecutionQueue(runtime_plan)
    plan_hash = _plan_hash(runtime_plan)
    catalog_hash = _catalog_hash(registry)
    recorder.record("plan_qualified", plan_hash=plan_hash, step_catalog_hash=catalog_hash)
    context: TestContext | None = None

    try:
        backend_env = backend.start(run_dir=artifacts.root)
        health = backend.health_check()
        recorder.record("backend_health", healthy=health.healthy, details=health.details)
        if not health.healthy:
            raise RuntimeError(f"backend failed health check: {health.details}")

        variables = VariableStore(recorder, initial_variables)
        reference = None
        if backend.name == "reference":
            socket_path = backend_env.get("AUTOMATION_HARNESS_SOCKET")
            if not socket_path:
                raise RuntimeError("reference backend did not provide AUTOMATION_HARNESS_SOCKET")
            reference = ReferenceClient(socket_path)

        context = TestContext(
            backend=backend.name,
            run_dir=artifacts.root,
            evidence=recorder,
            components=components,
            capabilities=backend.capabilities,
            steps=registry,
            globals=variables,
            reference=reference,
        )
        _write_catalog(artifacts.root, registry)
        _write_execution_state(artifacts.root, queue)

        while True:
            ready = queue.ready()
            if not ready:
                break
            node_id = ready[0]
            call = next(item for item in runtime_plan.steps if item.node_id == node_id)
            definition = registry.get(call.step_id)
            resolved_inputs = {
                key: _resolve_plan_value(value, variables)
                for key, value in call.inputs.items()
            }
            queue.start(node_id, resolved_inputs)
            recorder.record(
                "plan_step_started",
                node_id=node_id,
                step=definition.name,
                inputs=resolved_inputs,
            )
            _write_execution_state(artifacts.root, queue)
            try:
                signal_baseline = context.signals.snapshot()
                with context.execution_scope(call.scope):
                    invocation = context.run_step_detailed(
                        definition.name,
                        **resolved_inputs,
                        bind_outputs=call.outputs,
                    )
                    await_step_completion(
                        context,
                        call,
                        resolved_inputs,
                        signal_baseline=signal_baseline,
                    )
                bound_outputs = {name: invocation.outputs[name] for name in call.outputs}
                queue.complete(node_id, bound_outputs)
                # The VariableStore is authoritative; synchronize the queue view.
                queue.state.variables = variables.snapshot()
                result.passed += 1
                recorder.record(
                    "plan_step_finished",
                    node_id=node_id,
                    step=definition.name,
                    outputs=bound_outputs,
                )
            except Exception as exc:
                queue.fail(node_id, f"{type(exc).__name__}: {exc}")
                result.failed += 1
                result.exit_code = 1
                recorder.record(
                    "plan_step_failed",
                    node_id=node_id,
                    step=definition.name,
                    error=f"{type(exc).__name__}: {exc}",
                )
                _write_execution_state(artifacts.root, queue)
                break
            _write_execution_state(artifacts.root, queue)

        if result.exit_code is None:
            unfinished = [
                item for item in queue.state.steps.values()
                if item.status not in {StepStatus.PASSED, StepStatus.SKIPPED, StepStatus.CANCELLED}
            ]
            if unfinished:
                result.exit_code = 2
                blocked = [f"{item.node_id}:{item.status.value}" for item in unfinished]
                result.validation_errors.append("execution graph stalled: " + ", ".join(blocked))
                recorder.record("plan_execution_stalled", nodes=blocked)
            else:
                result.exit_code = 0
        if context.globals is not None:
            recorder.record("plan_globals_final", variables=context.globals.snapshot())
    except Exception as exc:
        result.exit_code = 3
        result.validation_errors.append(f"runtime error: {type(exc).__name__}: {exc}")
        recorder.record("plan_run_error", error=result.validation_errors[-1])
    finally:
        backend.stop()

    return _finalize(runtime_plan, backend, result, artifacts, recorder, initial_variables, registry=registry, compiled_artifact=compiled_artifact)


def _resolve_plan_value(value: Any, variables: VariableStore) -> Any:
    if isinstance(value, PlanVariableRef):
        return variables.resolve(VariableRef(value.path))
    if isinstance(value, list):
        return [_resolve_plan_value(item, variables) for item in value]
    if isinstance(value, tuple):
        return tuple(_resolve_plan_value(item, variables) for item in value)
    if isinstance(value, dict):
        return {key: _resolve_plan_value(item, variables) for key, item in value.items()}
    return value


def _write_execution_state(run_dir: Path, queue: ManagedExecutionQueue) -> None:
    temporary = run_dir / "execution_state.tmp"
    target = run_dir / "execution_state.json"
    temporary.write_text(json.dumps(queue.state.to_dict(), indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    temporary.replace(target)


def _write_catalog(run_dir: Path, registry) -> None:
    (run_dir / "registered_steps.json").write_text(
        json.dumps(
            {"domains": list(registry.domains()), "steps": [item.to_dict() for item in registry.definitions()]},
            indent=2,
            sort_keys=True,
            default=str,
        ) + "\n",
        encoding="utf-8",
    )


def _plan_hash(plan: TestPlan) -> str:
    payload = json.dumps(plan.to_dict(), sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _catalog_hash(registry) -> str:
    payload = json.dumps(
        [definition.to_dict() for definition in registry.definitions()],
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _finalize(plan, backend, result, artifacts, recorder, initial_variables, *, registry, compiled_artifact=None):
    result.finished_at = utc_now()
    recorder.record("plan_run_finished", exit_code=result.exit_code)
    artifacts.write_run_json(result.to_dict())
    artifacts.environment.write_text(
        json.dumps(
            {
                "execution_mode": "declarative-plan",
                "backend": backend.name,
                "capabilities": sorted(backend.capabilities),
                "platform": platform.platform(),
                "initial_globals": initial_variables,
                "plan_hash": _plan_hash(plan),
                "step_catalog_hash": _catalog_hash(registry),
                "allowed_step_risks": sorted(backend.allowed_step_risks),
                "compiled_artifact_sha256": compiled_artifact.digest if compiled_artifact else None,
            },
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    status = "PASS" if result.exit_code == 0 else "FAIL"
    lines = [
        f"{status}: {plan.name}",
        "Execution mode: declarative-plan",
        f"Backend: {backend.name}",
        f"Passed: {result.passed}",
        f"Failed: {result.failed}",
        f"Exit code: {result.exit_code}",
    ]
    if result.validation_errors:
        lines.append("Errors:")
        lines.extend(f"- {item}" for item in result.validation_errors)
    artifacts.summary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result
