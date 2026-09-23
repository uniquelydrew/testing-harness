from pathlib import Path

import pytest

from automation_harness.compat.java_target import child_environment


def _jar(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(b"agent")
    return path


def test_scoped_swing_launcher_preserves_non_harness_java_options(tmp_path):
    jar = _jar(tmp_path, "automation-harness-agent.jar")
    env = child_environment("swing", {
        "AUTOMATION_HARNESS_JAVA_AGENT": str(jar),
        "JAVA_TOOL_OPTIONS": "-Xmx1g -Ddemo=true",
    })

    assert "-Xmx1g" in env["JAVA_TOOL_OPTIONS"]
    assert "-Ddemo=true" in env["JAVA_TOOL_OPTIONS"]
    assert "-javaagent:%s" % jar in env["JAVA_TOOL_OPTIONS"]
    assert env["AUTOMATION_HARNESS_SCOPED_AGENT"] == "swing"


def test_scoped_launcher_rejects_existing_global_harness_agent(tmp_path):
    jar = _jar(tmp_path, "automation-harness-agent.jar")

    with pytest.raises(ValueError, match="already contains"):
        child_environment("swing", {
            "AUTOMATION_HARNESS_JAVA_AGENT": str(jar),
            "JAVA_TOOL_OPTIONS": "-javaagent:/tmp/automation-harness-old.jar",
        })


def test_scoped_launcher_requires_bootstrap_export(tmp_path):
    with pytest.raises(ValueError, match="AUTOMATION_HARNESS_JAVA_AGENT"):
        child_environment("swing", {})


def test_scoped_javafx_launcher_uses_only_javafx_agent(tmp_path):
    swing = _jar(tmp_path, "automation-harness-agent.jar")
    javafx = _jar(tmp_path, "automation-harness-javafx-agent.jar")

    env = child_environment("javafx", {
        "AUTOMATION_HARNESS_JAVA_AGENT": str(swing),
        "AUTOMATION_HARNESS_JAVAFX_AGENT": str(javafx),
    })

    assert "-javaagent:%s" % javafx in env["JAVA_TOOL_OPTIONS"]
    assert str(swing) not in env["JAVA_TOOL_OPTIONS"]
