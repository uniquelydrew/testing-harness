import argparse
import json
import os

from automation_harness import codex_runtime


def test_supervisor_records_process_lifecycle(tmp_path, monkeypatch):
    root = str(tmp_path / "runtime")
    session = str(tmp_path / "runtime" / "session")
    os.makedirs(session)
    monkeypatch.setattr(
        codex_runtime,
        "_preflight",
        lambda command: [{"name": "test", "ok": True, "detail": "qualified"}],
    )
    args = argparse.Namespace(
        runtime_root=root,
        session=session,
        command=["/bin/sh", "-c", "printf runtime-ok; printf runtime-err >&2"],
        cwd=str(tmp_path),
        interval=0.01,
    )

    assert codex_runtime._supervise(args) == 0

    with open(os.path.join(session, "manifest.json")) as handle:
        manifest = json.load(handle)
    assert manifest["state"] == "exited"
    assert manifest["returncode"] == 0
    assert manifest["signal"] is None
    assert manifest["application_pid"] > 0
    assert open(os.path.join(session, "stdout.log"), "rb").read() == b"runtime-ok"
    assert open(os.path.join(session, "stderr.log"), "rb").read() == b"runtime-err"
    events = [
        json.loads(line)
        for line in open(os.path.join(session, "runtime.jsonl"))
        if line.strip()
    ]
    assert events[0]["event"] == "application_started"
    assert events[-1]["event"] == "application_finished"


def test_status_reports_missing_runtime(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(codex_runtime, "_runtime_root", lambda: str(tmp_path))
    assert codex_runtime._status(argparse.Namespace()) == 1
    assert json.loads(capsys.readouterr().out)["state"] == "none"


def test_supervisor_marks_gdb_ptrace_block_as_diagnostic_blocked(tmp_path, monkeypatch):
    root = str(tmp_path / "runtime")
    session = str(tmp_path / "runtime" / "session")
    os.makedirs(session)
    monkeypatch.setattr(
        codex_runtime,
        "_preflight",
        lambda command: [{"name": "test", "ok": True, "detail": "qualified"}],
    )
    args = argparse.Namespace(
        runtime_root=root,
        session=session,
        command=["/bin/sh", "-c", "printf 'warning: ptrace: Operation not permitted\\n'; exit 127"],
        cwd=str(tmp_path),
        interval=0.01,
    )

    assert codex_runtime._supervise(args) == 0

    with open(os.path.join(session, "manifest.json")) as handle:
        manifest = json.load(handle)
    assert manifest["state"] == "diagnostic_blocked"
    assert manifest["diagnostic_error"] == "gdb_ptrace_not_permitted"
    events = [
        json.loads(line)
        for line in open(os.path.join(session, "runtime.jsonl"))
        if line.strip()
    ]
    assert events[-1]["event"] == "diagnostic_blocked"


def test_launch_gdb_uses_supported_logging_commands(tmp_path, monkeypatch, capsys):
    root = str(tmp_path / "runtime-root")
    session = str(tmp_path / "runtime-root" / "session-1")
    monkeypatch.setattr(codex_runtime, "_runtime_root", lambda: root)
    monkeypatch.setattr(codex_runtime, "_active", lambda _root: None)
    monkeypatch.setattr(codex_runtime, "_session_dir", lambda _root: session)
    monkeypatch.setattr(codex_runtime.shutil, "which", lambda name: "/usr/bin/gdb" if name == "gdb" else None)

    popen_calls = []

    class DummyPopen:
        def __init__(self, command, stdin=None, stdout=None, stderr=None, preexec_fn=None, close_fds=None):
            popen_calls.append(command)
            self.pid = 43210

        def poll(self):
            return None

    def fake_exists(path):
        return path == os.path.join(session, "manifest.json")

    monkeypatch.setattr(codex_runtime.subprocess, "Popen", DummyPopen)
    monkeypatch.setattr(codex_runtime.time, "sleep", lambda *_args: None)
    monkeypatch.setattr(codex_runtime.time, "time", lambda: 0.0)
    monkeypatch.setattr(codex_runtime.os.path, "exists", fake_exists)
    monkeypatch.setattr(
        codex_runtime,
        "_read_json",
        lambda path: {
            "version": 1,
            "state": "running",
            "session_dir": session,
            "command": ["stub"],
        } if path == os.path.join(session, "manifest.json") else {},
    )

    monkeypatch.setenv("AUTOMATION_HARNESS_VENV", str(tmp_path / "venv"))
    os.makedirs(tmp_path / "venv" / "bin")
    open(tmp_path / "venv" / "bin" / "python", "w").close()

    args = argparse.Namespace(
        interval=0.25,
        gdb=True,
        author_command=None,
        author_args=[],
    )

    assert codex_runtime._launch(args) == 0
    manifest = json.loads(capsys.readouterr().out)
    assert manifest["state"] == "running"

    supervisor = popen_calls[0]
    separator = supervisor.index("--")
    gdb_command = supervisor[separator + 1 :]
    assert gdb_command[:3] == ["/usr/bin/gdb", "--batch", "--quiet"]
    assert "-log" not in gdb_command
    assert ["-ex", "set logging overwrite on"] == gdb_command[5:7]
    assert ["-ex", "set logging on"] == gdb_command[7:9]
    assert gdb_command[-5:] == [
        "-return-child-result",
        "--args",
        str(tmp_path / "venv" / "bin" / "python"),
        "-m",
        "automation_harness.authoring.entrypoint",
    ]
    assert any(
        item == "set logging file %s" % os.path.join(session, "gdb-backtrace.txt")
        for item in gdb_command
    )


def test_status_reconciles_dead_running_process(tmp_path, monkeypatch, capsys):
    root = str(tmp_path / "runtime")
    session = str(tmp_path / "runtime" / "session")
    os.makedirs(session)
    manifest = {
        "version": 1,
        "state": "running",
        "session_dir": session,
        "application_pid": 43210,
    }
    codex_runtime._write_json(os.path.join(root, "active.json"), manifest)
    codex_runtime._write_json(os.path.join(session, "manifest.json"), manifest)
    monkeypatch.setattr(codex_runtime, "_runtime_root", lambda: root)
    monkeypatch.setattr(codex_runtime, "_alive", lambda _pid: False)

    assert codex_runtime._status(argparse.Namespace()) == 0
    observed = json.loads(capsys.readouterr().out)
    assert observed["state"] == "failed"
    assert observed["diagnostic_error"] == "supervised_process_not_alive"

    with open(os.path.join(session, "manifest.json")) as handle:
        persisted = json.load(handle)
    assert persisted["state"] == "failed"
    assert persisted["observed_state"] == "application_not_alive"


def test_prepare_runtime_root_falls_back_when_default_is_unwritable(tmp_path, monkeypatch):
    preferred = str(tmp_path / "readonly" / "codex-runtime")
    fallback_base = str(tmp_path / "fallback")
    real_makedirs = os.makedirs

    def fake_makedirs(path):
        if path == preferred:
            raise OSError(30, "Read-only file system")
        return real_makedirs(path)

    monkeypatch.setattr(codex_runtime.os, "makedirs", fake_makedirs)
    monkeypatch.setenv("TMPDIR", fallback_base)

    root = codex_runtime._prepare_runtime_root(preferred)

    assert root == os.path.join(
        fallback_base,
        "automation-harness-%d" % os.getuid(),
        "codex-runtime",
    )
    assert os.path.isdir(root)


def test_command_executes_authoring_module_main(tmp_path, monkeypatch):
    venv = str(tmp_path / "venv")
    os.makedirs(os.path.join(venv, "bin"))
    python = os.path.join(venv, "bin", "python")
    open(python, "w").close()
    monkeypatch.setenv("AUTOMATION_HARNESS_VENV", venv)

    args = argparse.Namespace(author_command=None)
    assert codex_runtime._command(args) == [
        python,
        "-m",
        "automation_harness.authoring.entrypoint",
    ]


def test_desktop_environment_recovers_gdm_xauthority(tmp_path, monkeypatch):
    runtime = tmp_path / "run"
    xauthority = runtime / "gdm" / "Xauthority"
    xauthority.parent.mkdir(parents=True)
    xauthority.write_text("cookie")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    monkeypatch.delenv("XAUTHORITY", raising=False)

    env = codex_runtime._desktop_environment()

    assert env["DISPLAY"] == ":0"
    assert env["XAUTHORITY"] == str(xauthority)


def test_environment_summary_records_desktop_session_fields(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("XAUTHORITY", "/tmp/xauth")
    monkeypatch.setenv("SESSION_MANAGER", "local/session")
    monkeypatch.setenv("GTK_MODULES", "gail:atk-bridge")
    monkeypatch.setenv("AT_SPI_BUS_ADDRESS", "unix:path=/tmp/atspi")

    summary = codex_runtime._environment_summary()

    env = summary["environment"]
    assert env["DISPLAY"] == ":0"
    assert env["XAUTHORITY"] == "/tmp/xauth"
    assert env["SESSION_MANAGER"] == "local/session"
    assert env["GTK_MODULES"] == "gail:atk-bridge"
    assert env["AT_SPI_BUS_ADDRESS"] == "unix:path=/tmp/atspi"
    assert "pid" in summary["process"]
    assert "pgid" in summary["process"]
    assert "sid" in summary["process"]


def test_preflight_rejects_unreadable_xauthority(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setenv("XAUTHORITY", "/definitely/missing/Xauthority")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/tmp/dbus")
    monkeypatch.setattr(codex_runtime.os.path, "exists", lambda path: True if path == "/bin/true" else os.path.exists(path))

    checks = codex_runtime._preflight(["/bin/true"])
    xauth = [item for item in checks if item["name"] == "xauthority"][0]

    assert xauth["ok"] is False
