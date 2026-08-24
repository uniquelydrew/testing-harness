def test_registered_steps_can_be_reused_and_composed_without_importing_implementations(ctx):
    result = ctx.run_step(
        "track.create_and_follow",
        "registered-alpha",
        x=15.0,
        y=12.0,
        vx=0.0,
        vy=0.0,
    )
    assert result["created"]["track_id"] == "registered-alpha"
    ctx.run_step("validation.track.followed", "registered-alpha")

    ctx.run_step("threat.level.set", "medium")
    actual = ctx.run_step("threat.level.get")
    ctx.run_step("validation.equal", "registered_threat_level", actual, "MEDIUM")
