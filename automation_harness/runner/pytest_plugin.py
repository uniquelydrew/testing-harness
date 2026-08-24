from __future__ import annotations

import os

import pytest

from automation_harness.core.test_context import TestContext


@pytest.fixture
def ctx(request) -> TestContext:
    context = TestContext.from_environment()
    if context.reference is not None:
        context.reference.request("reset")
    setattr(request.node, "_automation_context", context)
    context.evidence.record("test_started", test=request.node.nodeid, backend=context.backend)
    try:
        yield context
    finally:
        if context.globals is not None:
            context.evidence.record(
                "test_globals_final",
                test=request.node.nodeid,
                variables=context.globals.snapshot(),
            )
        context.evidence.record("test_finished", test=request.node.nodeid)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if not report.failed or call.when not in {"setup", "call"}:
        return
    context = getattr(item, "_automation_context", None)
    if context is None:
        return
    context.evidence.record(
        "test_failure",
        test=item.nodeid,
        phase=call.when,
        longrepr=str(report.longrepr),
    )
    if not os.environ.get("DISPLAY"):
        return
    try:
        from automation_harness.drivers.vision_driver import VisionDriver

        path = VisionDriver(context).capture(name=f"failure-{item.name}")
        context.evidence.record(
            "failure_screenshot_captured",
            test=item.nodeid,
            path=str(path.relative_to(context.run_dir)),
        )
    except Exception as exc:
        context.evidence.record(
            "failure_screenshot_error",
            test=item.nodeid,
            error=f"{type(exc).__name__}: {exc}",
        )
