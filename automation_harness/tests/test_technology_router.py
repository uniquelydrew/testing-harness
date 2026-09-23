import pytest

from automation_harness.core.runtime_observation import Framework
from automation_harness.core.technology_router import (
    AdapterKind,
    TargetContext,
    TechnologyRouter,
    TechnologyRoutingError,
)


def _available(*adapters):
    return {item: True for item in adapters}


def test_javafx_endpoint_wins_over_cohosted_agent_without_backend_race():
    decision = TechnologyRouter().route(
        TargetContext(
            process_id=42, window_id="0x100", framework_hint=Framework.SWING,
            java_agent_pids=frozenset({42}), javafx_pids=frozenset({42}),
        ),
        availability=_available(
            AdapterKind.JAVA_AGENT, AdapterKind.JAVAFX, AdapterKind.ATSPI,
        ),
    )
    assert decision.adapter is AdapterKind.JAVAFX
    assert decision.framework is Framework.JAVAFX
    assert decision.fallback is None


def test_javafx_endpoint_is_authoritative_when_mixed_agent_is_absent():
    decision = TechnologyRouter().route(
        TargetContext(
            process_id=73, window_id="0x200",
            framework_hint=Framework.JAVAFX,
            javafx_pids=frozenset({73}),
        ),
        availability=_available(AdapterKind.JAVAFX, AdapterKind.ATSPI),
    )
    assert decision.adapter is AdapterKind.JAVAFX
    assert decision.fallback is None


def test_known_javafx_target_never_silently_falls_back_to_atspi():
    with pytest.raises(TechnologyRoutingError, match="substitution is prohibited"):
        TechnologyRouter().route(
            TargetContext(
                process_id=73, window_id="0x200",
                framework_hint=Framework.JAVAFX,
            ),
            availability=_available(AdapterKind.ATSPI),
        )


def test_registered_rendering_surface_uses_rendered_adapter():
    decision = TechnologyRouter().route(
        TargetContext(
            process_id=91, window_id="0x300", rendered_surface=True,
            java_agent_pids=frozenset({91}),
        ),
        availability=_available(AdapterKind.RENDERED, AdapterKind.JAVA_AGENT),
    )
    assert decision.adapter is AdapterKind.RENDERED
    assert decision.framework is Framework.RENDERED


def test_uninstrumented_native_target_uses_atspi():
    decision = TechnologyRouter().route(
        TargetContext(process_id=12, window_id="0x400"),
        availability=_available(AdapterKind.ATSPI),
    )
    assert decision.adapter is AdapterKind.ATSPI
    assert decision.framework is Framework.ATSPI


def test_missing_authoritative_adapter_is_a_routing_failure():
    with pytest.raises(TechnologyRoutingError, match="authoritative java_agent"):
        TechnologyRouter().route(
            TargetContext(
                process_id=42, window_id="0x100",
                framework_hint=Framework.SWING,
                java_agent_pids=frozenset({42}),
            ),
            availability=_available(AdapterKind.ATSPI),
        )
