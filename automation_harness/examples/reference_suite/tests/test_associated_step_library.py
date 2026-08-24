def test_manifest_associated_step_library_is_available_without_import(ctx):
    assert ctx.run_step("workflow.raise_threat_and_verify", "high") == "HIGH"
