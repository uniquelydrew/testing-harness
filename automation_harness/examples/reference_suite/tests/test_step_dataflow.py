def test_registered_step_outputs_feed_inputs_and_test_globals(ctx):
    assert ctx.globals.get("history") == []
    assert ctx.globals.get("session.phase") == "initialized"

    ctx.run_step(
        "track.create_moving",
        "dataflow-alpha",
        x=12.0,
        y=5.0,
        vx=8.0,
        vy=0.0,
        bind_outputs={"track_id": "active_track", "x": "initial_x", "track": "created_track"},
    )
    ctx.globals.append("history", {"event": "created", "track_id": ctx.ref("active_track")})

    ctx.run_step(
        "track.wait_for_motion",
        ctx.ref("active_track"),
        initial_x=ctx.ref("initial_x"),
        bind_outputs={"x": "moved_x"},
    )
    ctx.run_step(
        "track.follow",
        ctx.ref("active_track"),
        bind_outputs={"followed": "is_followed"},
    )
    ctx.run_step("validation.equal", "dataflow_followed", ctx.ref("is_followed"), True)

    ctx.globals.update(
        "session",
        {
            "phase": "followed",
            "track_id": ctx.ref("active_track"),
            "moved_x": ctx.ref("moved_x"),
        },
    )
    ctx.globals.append("history", ctx.ref("session"))

    ctx.run_step(
        "workflow.raise_threat_and_verify",
        "high",
        bind_outputs={"level": "current_threat"},
    )
    ctx.globals.append("history", {"event": "threat", "level": ctx.ref("current_threat")})

    assert ctx.globals.get("session.track_id") == "dataflow-alpha"
    assert ctx.globals.get("session.phase") == "followed"
    assert ctx.globals.get("history.0.track_id") == "dataflow-alpha"
    assert ctx.globals.get("history.2.level") == "HIGH"
