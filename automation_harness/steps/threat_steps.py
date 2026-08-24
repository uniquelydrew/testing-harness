from __future__ import annotations

from automation_harness.core.step_registry import step
from automation_harness.core.test_context import TestContext


@step(
    "threat.level.set",
    domain="threat",
    description="Set the current synthetic/reference threat level.",
    capabilities={"threat-state"},
    risk="synthetic_control",
    aliases=("set_threat_level",),
    outputs={"level": "$"},
)
def set_threat_level(ctx: TestContext, level: str) -> str:
    return ctx.require_services().require_threat().set_level(level)


@step(
    "threat.level.get",
    domain="threat",
    description="Read the current synthetic/reference threat level.",
    capabilities={"threat-state"},
    aliases=("get_threat_level",),
    outputs={"level": "$"},
)
def get_threat_level(ctx: TestContext) -> str:
    return ctx.require_services().require_threat().get_level()
