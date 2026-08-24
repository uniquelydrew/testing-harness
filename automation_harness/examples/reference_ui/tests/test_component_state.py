def test_reference_component_state_is_typed_and_waitable(ctx):
    disabled = ctx.component("reference.state.disabled_action").state()
    assert disabled.present is True
    assert disabled.visible is True
    assert disabled.enabled is False

    menu = ctx.component("reference.state.options_menu")
    assert menu.state().expanded is False

    # Synthetic setup channel changes reference state; observation still flows
    # through the component state abstraction rather than assuming the action.
    ctx.require_reference().request(
        "set_ui_component_state",
        component_id="state.options_menu",
        expanded=True,
    )
    observed = menu.wait_for(expanded=True, timeout=1.0)
    assert observed.expanded is True
