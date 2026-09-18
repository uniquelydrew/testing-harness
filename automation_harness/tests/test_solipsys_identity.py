from automation_harness.core.solipsys_identity import (
    locator_is_complete,
    locators_match,
    semantic_identity,
)


def _identity(value="2"):
    return {
        "rendered_class": "com.solipsys.tdf.track.DefaultTrackVelocityDisplay2D",
        "track_class": "com.solipsys.msct.track.report.MSCTTrackReport",
        "track_identity_key": "field:identity",
        "track_identity_value": value,
    }


def test_semantic_identity_requires_all_four_locator_values():
    complete = _identity()
    assert locator_is_complete(complete)
    assert semantic_identity(complete)[-1] == "2"
    complete.pop("track_class")
    assert not locator_is_complete(complete)
    assert semantic_identity(complete) is None


def test_locator_matching_ignores_runtime_state():
    assert locators_match(
        dict(_identity(), rendered_object_ref="display-a", position=[737, 480]),
        {"window": "MSCT"},
        dict(_identity(), rendered_object_ref="display-b", position=[1297, 227]),
        {"window": "MSCT"},
    )


def test_locator_matching_rejects_identity_or_scope_conflict():
    assert not locators_match(_identity("2"), {}, _identity("3"), {})
    assert not locators_match(
        _identity("2"), {"window": "MSCT A"},
        _identity("2"), {"window": "MSCT B"},
    )
