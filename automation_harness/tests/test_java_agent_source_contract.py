from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_swing_recorder_emits_press_and_release_pointer_observations():
    source = (
        ROOT
        / "java-agent"
        / "src"
        / "java8"
        / "java"
        / "automation"
        / "harness"
        / "agent"
        / "SwingRecorder.java"
    ).read_text(encoding="utf-8")

    assert 'emitPointerObservation(destination, event, screen, pressTarget, "pressed")' in source
    assert 'emitPointerObservation(destination, event, screen, target, "released")' in source
    assert "componentAtEventWindow" in source


def test_swing_recorder_suppresses_unresolved_render_surface_press_highlight():
    source = (
        ROOT
        / "java-agent"
        / "src"
        / "java8"
        / "java"
        / "automation"
        / "harness"
        / "agent"
        / "SwingRecorder.java"
    ).read_text(encoding="utf-8")

    assert "Do not flash the entire render canvas" in source
    assert '!Boolean.TRUE.equals(promotion.get("promoted"))' in source


def test_mixed_agent_server_exposes_standardized_swing_operations():
    source = (
        ROOT
        / "java-agent"
        / "src"
        / "java8"
        / "java"
        / "automation"
        / "harness"
        / "agent"
        / "AgentServer.java"
    ).read_text(encoding="utf-8")

    for endpoint in (
        '"/get_text"',
        '"/set_text"',
        '"/select_child"',
        '"/get_value"',
        '"/set_value"',
        '"/select_menu_path"',
        '"/focus"',
        '"/activate_window"',
    ):
        assert endpoint in source



def test_agent_server_uses_valid_java_character_escapes_for_json_strings():
    source = (
        ROOT
        / "java-agent"
        / "src"
        / "java8"
        / "java"
        / "automation"
        / "harness"
        / "agent"
        / "AgentServer.java"
    ).read_text(encoding="utf-8")

    assert "if (ch != '\\\\'" not in source
    assert "case '\\\\': result.append('\\\\');" in source
    assert "case 'n': result.append('\\n');" in source
    assert "case 't': result.append('\\t');" in source
