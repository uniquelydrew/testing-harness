from __future__ import annotations

from automation_harness.core.step_registry import step
from automation_harness.core.test_context import TestContext


@step(
    "workflow.raise_threat_and_verify",
    domain="workflow",
    description="Reusable bundle workflow composed entirely from previously registered steps.",
    capabilities={"threat-state"},
    outputs={"level": "$"},
)
def raise_threat_and_verify(ctx: TestContext, level: str) -> str:
    expected = level.upper()
    ctx.run_step("threat.level.set", expected)
    actual = ctx.run_step("threat.level.get")
    ctx.run_step("validation.equal", "workflow_threat_level", actual, expected)
    return actual
