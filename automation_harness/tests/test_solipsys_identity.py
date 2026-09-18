from automation_harness.core.solipsys_identity import (
    locator_is_complete,
    locators_match,
    semantic_identity,
    visible_identity_status,
)


def _identity(value="2", key="getTrackId"):
    return {
        "rendered_class": "com.solipsys.tdf.track.DefaultTrackVelocityDisplay2D",
        "track_class": "com.solipsys.msct.track.report.MSCTTrackReport",
        "track_identity_key": key,
        "track_identity_value": value,
    }


def test_semantic_identity_requires_all_four_locator_values():
    complete = _identity()
    assert locator_is_complete(complete)
    assert semantic_identity(complete)[-1] == "2"
    complete.pop("track_class")
    assert not locator_is_complete(complete)
    assert semantic_identity(complete) is None


def test_field_identity_is_explicitly_disqualified_as_durable_identity():
    identity = _identity(key="field:identity")
    assert not locator_is_complete(identity)
    assert semantic_identity(identity) is None


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


def test_visible_identity_status_distinguishes_unique_ambiguous_and_unknown():
    assert visible_identity_status({
        "identity_visible_match_count": 1,
        "identity_unique_in_visible_scope": True,
    }) == "candidate_unique"
    assert visible_identity_status({
        "identity_visible_match_count": 2,
        "identity_unique_in_visible_scope": False,
    }) == "ambiguous"
    assert visible_identity_status({
        "identity_visible_match_count": 0,
    }) == "unavailable"
    assert visible_identity_status({}) == "unverified"
