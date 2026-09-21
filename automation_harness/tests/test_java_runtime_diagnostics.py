import json

from automation_harness.drivers.java_runtime_diagnostics import (
    collect_java_runtime_diagnostics,
    java_runtime_for_pid,
)


def test_collect_runtime_diagnostics_requires_live_agent_discovery(tmp_path):
    runtime = {
        "pid": 5887,
        "java_version": "11.0.24",
        "java_vendor": "Red Hat, Inc.",
        "java_home": "/usr/lib/jvm/java-11",
        "java_vm_name": "OpenJDK 64-Bit Server VM",
        "java_vm_version": "11.0.24+8-LTS",
        "java_class_version": "55.0",
        "input_arguments": ["-Xmx4g"],
    }
    (tmp_path / "java-5887-runtime.json").write_text(json.dumps(runtime), encoding="utf-8")

    assert collect_java_runtime_diagnostics(tmp_path) == ()

    (tmp_path / "java-5887.json").write_text("{}", encoding="utf-8")
    snapshots = collect_java_runtime_diagnostics(tmp_path)

    assert len(snapshots) == 1
    assert snapshots[0]["pid"] == 5887
    assert snapshots[0]["java_version"] == "11.0.24"
    assert snapshots[0]["java_class_version"] == "55.0"
    assert snapshots[0]["input_arguments"] == ["-Xmx4g"]


def test_runtime_lookup_is_pid_specific(tmp_path):
    for pid, version in ((5887, "11.0.24"), (6001, "17.0.12")):
        (tmp_path / ("java-%s.json" % pid)).write_text("{}", encoding="utf-8")
        (tmp_path / ("java-%s-runtime.json" % pid)).write_text(
            json.dumps({"pid": pid, "java_version": version}), encoding="utf-8",
        )

    assert java_runtime_for_pid(5887, tmp_path)["java_version"] == "11.0.24"
    assert java_runtime_for_pid(6001, tmp_path)["java_version"] == "17.0.12"
    assert java_runtime_for_pid(9999, tmp_path) is None
