"""Detached runtime orchestration for Codex-assisted desktop debugging.

This module intentionally uses only the Python 3.6 standard library.  The
supervisor survives the shell/codex process that launched it, owns a separate
GUI process group, and leaves a machine-readable handoff in the runtime root.
"""
import argparse
import datetime
import json
import os
import platform
import resource
import shutil
import signal
import subprocess
import sys
import time


def _utc():
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _runtime_root():
    explicit = os.environ.get("AUTOMATION_HARNESS_CODEX_RUNTIME_DIR")
    if explicit:
        preferred = os.path.abspath(os.path.expanduser(explicit))
    else:
        state = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
        preferred = os.path.join(state, "automation-harness", "codex-runtime")
    return _prepare_runtime_root(preferred)


def _prepare_runtime_root(root):
    try:
        if not os.path.isdir(root):
            os.makedirs(root)
        probe = os.path.join(root, ".write-probe-%d" % os.getpid())
        with open(probe, "w") as handle:
            handle.write("ok\n")
        os.unlink(probe)
        return root
    except OSError:
        fallback = os.path.join(
            os.environ.get("TMPDIR") or "/tmp",
            "automation-harness-%d" % os.getuid(),
            "codex-runtime",
        )
        if not os.path.isdir(fallback):
            os.makedirs(fallback)
        return fallback


def _desktop_environment():
    env = os.environ.copy()
    if env.get("DISPLAY") and not env.get("XAUTHORITY"):
        runtime_dir = env.get("XDG_RUNTIME_DIR") or ("/run/user/%d" % os.getuid())
        candidates = (
            os.path.join(runtime_dir, "gdm", "Xauthority"),
            os.path.join(os.path.expanduser("~"), ".Xauthority"),
        )
        for candidate in candidates:
            if os.path.isfile(candidate) and os.access(candidate, os.R_OK):
                env["XAUTHORITY"] = candidate
                break
    return env


def _process_identity(pid=None):
    pid = os.getpid() if pid is None else int(pid)
    result = {"pid": pid}
    try:
        result["pgid"] = os.getpgid(pid)
    except OSError:
        result["pgid"] = None
    try:
        result["sid"] = os.getsid(pid)
    except OSError:
        result["sid"] = None
    try:
        with open("/proc/%d/stat" % pid) as handle:
            fields = handle.read().split()
        result["ppid"] = int(fields[3]) if len(fields) > 3 else None
    except (IOError, OSError, ValueError):
        result["ppid"] = None
    return result


def _enable_core_dumps():
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_CORE)
        target = hard if hard != resource.RLIM_INFINITY else resource.RLIM_INFINITY
        resource.setrlimit(resource.RLIMIT_CORE, (target, hard))
    except (ValueError, OSError):
        pass
    os.setsid()


def _write_json(path, value):
    tmp = path + ".tmp"
    with open(tmp, "w") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.rename(tmp, path)


def _append_jsonl(path, value):
    with open(path, "a") as handle:
        handle.write(json.dumps(value, sort_keys=True) + "\n")
        handle.flush()


def _read_text(path):
    try:
        with open(path) as handle:
            return handle.read()
    except IOError:
        return ""


def _read_json(path):
    with open(path) as handle:
        return json.load(handle)


def _active_path(root):
    return os.path.join(root, "active.json")


def _active(root):
    path = _active_path(root)
    if not os.path.exists(path):
        return None
    try:
        return _read_json(path)
    except (IOError, ValueError):
        return None


def _alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def _proc_status(pid):
    result = {}
    try:
        with open("/proc/%d/status" % int(pid)) as handle:
            for line in handle:
                if ":" in line:
                    key, value = line.split(":", 1)
                    if key in ("State", "VmRSS", "VmSize", "Threads", "FDSize"):
                        result[key] = value.strip()
    except IOError:
        pass
    return result


def _process_tree(pid):
    try:
        output = subprocess.check_output(
            ["ps", "-eo", "pid,ppid,pgid,sid,tpgid,tty,stat,etime,comm,args"],
            universal_newlines=True,
        )
    except Exception as exc:
        return "ps unavailable: %s: %s\n" % (type(exc).__name__, exc)
    lines = output.splitlines()
    selected = [lines[0]] if lines else []
    wanted = set([int(pid)])
    changed = True
    while changed:
        changed = False
        for line in lines[1:]:
            fields = line.strip().split(None, 3)
            if len(fields) < 3:
                continue
            try:
                child, parent = int(fields[0]), int(fields[1])
            except ValueError:
                continue
            if parent in wanted and child not in wanted:
                wanted.add(child)
                changed = True
    for line in lines[1:]:
        fields = line.strip().split(None, 1)
        if fields:
            try:
                if int(fields[0]) in wanted:
                    selected.append(line)
            except ValueError:
                pass
    return "\n".join(selected) + "\n"


def _environment_summary():
    keys = (
        "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS",
        "SESSION_MANAGER", "XDG_SESSION_TYPE", "XDG_RUNTIME_DIR", "DESKTOP_SESSION",
        "GTK_MODULES", "GTK_A11Y", "NO_AT_BRIDGE", "QT_ACCESSIBILITY",
        "AT_SPI_BUS_ADDRESS",
        "AUTOMATION_HARNESS_ROOT", "AUTOMATION_HARNESS_VENV",
        "AUTOMATION_HARNESS_RUNTIME_DIR", "AUTOMATION_HARNESS_JAVAFX_AGENT",
        "AUTOMATION_HARNESS_JAVA_AGENT", "AUTOMATION_HARNESS_JAVA_ATK_WRAPPER",
    )
    return {
        "captured_at": _utc(),
        "platform": platform.platform(),
        "python": sys.version,
        "cwd": os.getcwd(),
        "process": _process_identity(),
        "environment": dict((key, os.environ.get(key)) for key in keys),
        "executables": dict(
            (name, shutil.which(name) if hasattr(shutil, "which") else None)
            for name in ("automation-author", "gdb", "java", "jps", "ps")
        ),
    }


def _preflight(command):
    checks = []
    def check(name, ok, detail):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})
    display = os.environ.get("DISPLAY")
    session_type = (os.environ.get("XDG_SESSION_TYPE") or "").lower()
    check("display", bool(display), display or "DISPLAY is unset")
    if display and session_type != "wayland":
        xauthority = os.environ.get("XAUTHORITY")
        check(
            "xauthority",
            bool(xauthority and os.path.isfile(xauthority) and os.access(xauthority, os.R_OK)),
            xauthority or "XAUTHORITY is unset",
        )
    check("dbus", bool(os.environ.get("DBUS_SESSION_BUS_ADDRESS")),
          "present" if os.environ.get("DBUS_SESSION_BUS_ADDRESS") else "DBUS_SESSION_BUS_ADDRESS is unset")
    executable = command[0]
    resolved = executable if os.path.isabs(executable) else (
        shutil.which(executable) if hasattr(shutil, "which") else None
    )
    check("authoring_executable", bool(resolved and os.path.exists(resolved)), resolved or executable)
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        check("gtk", True, "Gtk 3 importable")
    except Exception as exc:
        check("gtk", False, "%s: %s" % (type(exc).__name__, exc))
    try:
        import pyatspi
        check("atspi", True, "pyatspi importable")
    except Exception as exc:
        check("atspi", False, "%s: %s" % (type(exc).__name__, exc))
    return checks


def _command(args):
    if args.author_command:
        return list(args.author_command)
    venv = os.environ.get("AUTOMATION_HARNESS_VENV")
    candidate = os.path.join(venv, "bin", "python") if venv else None
    if candidate and os.path.exists(candidate):
        return [candidate, "-m", "automation_harness.authoring.entrypoint"]
    return [sys.executable, "-m", "automation_harness.authoring.entrypoint"]


def _session_dir(root):
    stamp = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    base = os.path.join(root, stamp + "-%d" % os.getpid())
    path = base
    suffix = 1
    while os.path.exists(path):
        path = base + "-%d" % suffix
        suffix += 1
    os.makedirs(path)
    return path


def _supervise(args):
    root = args.runtime_root
    session = args.session
    command = args.command
    stdout_path = os.path.join(session, "stdout.log")
    stderr_path = os.path.join(session, "stderr.log")
    runtime_path = os.path.join(session, "runtime.jsonl")
    manifest_path = os.path.join(session, "manifest.json")
    stop_path = os.path.join(session, "stop.request")

    _write_json(os.path.join(session, "environment.json"), _environment_summary())
    preflight = _preflight(command)
    _write_json(os.path.join(session, "preflight.json"), preflight)
    if not all(item["ok"] for item in preflight):
        manifest = {
            "version": 1, "state": "preflight_failed", "started_at": _utc(),
            "finished_at": _utc(), "session_dir": session, "command": command,
            "supervisor_pid": os.getpid(), "preflight": preflight,
        }
        _write_json(manifest_path, manifest)
        _write_json(_active_path(root), manifest)
        return 2

    out = open(stdout_path, "ab", 0)
    err = open(stderr_path, "ab", 0)
    started = _utc()
    try:
        child_env = _desktop_environment()
        child_env.setdefault("PYTHONFAULTHANDLER", "1")
        child_env["AUTOMATION_HARNESS_CODEX_SESSION_DIR"] = session
        diagnostic_dir = os.path.join(args.cwd, "artifacts", "crash-investigation")
        if not os.path.isdir(diagnostic_dir):
            try:
                os.makedirs(diagnostic_dir)
            except OSError:
                pass
        child_env["AUTOMATION_HARNESS_DIAGNOSTIC_DIR"] = diagnostic_dir
        process = subprocess.Popen(
            command, cwd=args.cwd, env=child_env,
            stdout=out, stderr=err, preexec_fn=_enable_core_dumps,
        )
    except Exception as exc:
        manifest = {
            "version": 1, "state": "launch_failed", "started_at": started,
            "finished_at": _utc(), "session_dir": session, "command": command,
            "supervisor_pid": os.getpid(),
            "error": "%s: %s" % (type(exc).__name__, exc),
        }
        _write_json(manifest_path, manifest)
        _write_json(_active_path(root), manifest)
        return 3

    manifest = {
        "version": 1, "state": "running", "started_at": started,
        "session_dir": session, "command": command, "cwd": args.cwd,
        "supervisor_pid": os.getpid(), "application_pid": process.pid,
        "supervisor_process": _process_identity(),
        "application_process": _process_identity(process.pid),
        "application_pgid": os.getpgid(process.pid),
        "application_sid": os.getsid(process.pid),
        "stdout": stdout_path, "stderr": stderr_path, "runtime_log": runtime_path,
    }
    _write_json(manifest_path, manifest)
    _write_json(_active_path(root), manifest)
    _append_jsonl(runtime_path, {"event": "application_started", "timestamp": _utc(), "pid": process.pid})

    stop_sent = False
    while process.poll() is None:
        if os.path.exists(stop_path) and not stop_sent:
            stop_sent = True
            _append_jsonl(runtime_path, {"event": "stop_requested", "timestamp": _utc()})
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            except OSError:
                pass
        _append_jsonl(runtime_path, {
            "event": "heartbeat", "timestamp": _utc(), "pid": process.pid,
            "process": _proc_status(process.pid),
        })
        time.sleep(max(0.25, args.interval))

    code = process.returncode
    state = "exited" if code == 0 else ("signaled" if code < 0 else "failed")
    stdout_text = _read_text(stdout_path)
    stderr_text = _read_text(stderr_path)
    gdb_ptrace_blocked = (
        "ptrace: Operation not permitted" in stdout_text
        or "ptrace: Operation not permitted" in stderr_text
    )
    if gdb_ptrace_blocked:
        state = "diagnostic_blocked"
    manifest.update({
        "state": state, "finished_at": _utc(), "returncode": code,
        "signal": -code if code < 0 else None,
    })
    if gdb_ptrace_blocked:
        manifest["diagnostic_error"] = "gdb_ptrace_not_permitted"
    _write_json(manifest_path, manifest)
    _write_json(_active_path(root), manifest)
    _append_jsonl(runtime_path, {
        "event": "application_finished", "timestamp": _utc(),
        "returncode": code, "signal": -code if code < 0 else None,
    })
    if gdb_ptrace_blocked:
        _append_jsonl(runtime_path, {
            "event": "diagnostic_blocked",
            "timestamp": _utc(),
            "reason": "gdb_ptrace_not_permitted",
        })
    with open(os.path.join(session, "process-tree-final.txt"), "w") as handle:
        handle.write(_process_tree(process.pid))
    out.close()
    err.close()
    return 0


def _launch(args):
    root = _runtime_root()
    current = _active(root)
    current = _reconcile_stale_manifest(root, current)
    if current and current.get("state") == "running" and _alive(current.get("application_pid")):
        print(json.dumps(current, indent=2, sort_keys=True))
        return 4

    session = _session_dir(root)
    author_args = list(args.author_args or [])
    if author_args and author_args[0] == "--":
        author_args = author_args[1:]
    command = _command(args) + author_args
    if args.gdb:
        gdb = shutil.which("gdb") if hasattr(shutil, "which") else None
        if not gdb:
            print("gdb is not available", file=sys.stderr)
            return 5
        backtrace = os.path.join(session, "gdb-backtrace.txt")
        command = [
            gdb, "--batch", "--quiet",
            "-ex", "set pagination off",
            "-ex", "set logging file %s" % backtrace,
            "-ex", "set logging overwrite on",
            "-ex", "set logging on",
            "-ex", "run",
            "-ex", "thread apply all bt full",
            "-ex", "info sharedlibrary",
            "-return-child-result",
            "--args",
        ] + command
    supervisor = [
        sys.executable, "-m", "automation_harness.codex_runtime", "_supervise",
        "--runtime-root", root, "--session", session, "--cwd", os.getcwd(),
        "--interval", str(args.interval), "--",
    ] + command
    devnull = open(os.devnull, "rb")
    detached = open(os.path.join(session, "supervisor.log"), "ab", 0)
    proc = subprocess.Popen(
        supervisor, stdin=devnull, stdout=detached, stderr=detached,
        env=_desktop_environment(), preexec_fn=os.setsid, close_fds=True,
    )
    deadline = time.time() + 5.0
    manifest = None
    while time.time() < deadline:
        path = os.path.join(session, "manifest.json")
        if os.path.exists(path):
            manifest = _read_json(path)
            break
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    if manifest is None:
        manifest = {
            "version": 1, "state": "supervisor_starting", "session_dir": session,
            "supervisor_pid": proc.pid, "command": command,
        }
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0 if manifest.get("state") in ("running", "supervisor_starting") else 2


def _reconcile_stale_manifest(root, manifest):
    if not manifest or manifest.get("state") != "running":
        return manifest
    if _alive(manifest.get("application_pid")):
        return manifest
    reconciled = dict(manifest)
    reconciled.update({
        "state": "failed",
        "finished_at": _utc(),
        "diagnostic_error": "supervised_process_not_alive",
        "observed_state": "application_not_alive",
    })
    session = reconciled.get("session_dir")
    if session:
        manifest_path = os.path.join(session, "manifest.json")
        if os.path.isdir(session):
            _write_json(manifest_path, reconciled)
            runtime_path = os.path.join(session, "runtime.jsonl")
            _append_jsonl(runtime_path, {
                "event": "diagnostic_failed",
                "timestamp": _utc(),
                "reason": "supervised_process_not_alive",
            })
    _write_json(_active_path(root), reconciled)
    return reconciled


def _status(args):
    root = _runtime_root()
    manifest = _active(root)
    if manifest is None:
        print(json.dumps({"state": "none", "runtime_root": root}, indent=2))
        return 1
    manifest = _reconcile_stale_manifest(root, manifest)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


def _snapshot(args):
    root = _runtime_root()
    manifest = _active(root)
    manifest = _reconcile_stale_manifest(root, manifest)
    if manifest is None:
        print("No runtime session.", file=sys.stderr)
        return 1
    session = manifest["session_dir"]
    stamp = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    target = os.path.join(session, "snapshot-" + stamp)
    os.makedirs(target)
    _write_json(os.path.join(target, "manifest.json"), manifest)
    _write_json(os.path.join(target, "environment.json"), _environment_summary())
    pid = manifest.get("application_pid")
    if pid:
        _write_json(os.path.join(target, "process-status.json"), _proc_status(pid))
        with open(os.path.join(target, "process-tree.txt"), "w") as handle:
            handle.write(_process_tree(pid))
        for name in ("status", "limits", "maps"):
            source = "/proc/%s/%s" % (pid, name)
            try:
                shutil.copyfile(source, os.path.join(target, "proc-" + name + ".txt"))
            except IOError:
                pass
    print(target)
    return 0


def _stop(args):
    root = _runtime_root()
    manifest = _active(root)
    manifest = _reconcile_stale_manifest(root, manifest)
    if manifest is None or manifest.get("state") != "running":
        print(json.dumps(manifest or {"state": "none"}, indent=2, sort_keys=True))
        return 1
    with open(os.path.join(manifest["session_dir"], "stop.request"), "w") as handle:
        handle.write(_utc() + "\n")
    print(json.dumps({"state": "stop_requested", "application_pid": manifest.get("application_pid"),
                      "session_dir": manifest["session_dir"]}, indent=2, sort_keys=True))
    return 0


def _report(args):
    root = _runtime_root()
    manifest = _active(root)
    manifest = _reconcile_stale_manifest(root, manifest)
    if manifest is None:
        print("No runtime session.")
        return 1
    session = manifest["session_dir"]
    print("Automation Harness Codex runtime")
    print("state: %s" % manifest.get("state"))
    print("session: %s" % session)
    print("application pid: %s" % manifest.get("application_pid"))
    if "returncode" in manifest:
        print("return code: %s" % manifest.get("returncode"))
    if manifest.get("signal"):
        print("signal: %s" % manifest.get("signal"))
    print("stdout: %s" % os.path.join(session, "stdout.log"))
    print("stderr: %s" % os.path.join(session, "stderr.log"))
    print("runtime: %s" % os.path.join(session, "runtime.jsonl"))
    return 0


def _parser():
    parser = argparse.ArgumentParser(description="Detached Automation Harness runtime orchestrator")
    sub = parser.add_subparsers(dest="action")

    launch = sub.add_parser("launch", help="launch and supervise automation-author")
    launch.add_argument("--interval", type=float, default=0.25)
    launch.add_argument("--gdb", action="store_true", help="run the GUI under batch GDB and capture a native backtrace")
    launch.add_argument("--author-command", nargs="+")
    launch.add_argument("author_args", nargs=argparse.REMAINDER)

    sub.add_parser("status", help="print the active runtime manifest")
    sub.add_parser("snapshot", help="capture process/runtime state without stopping")
    sub.add_parser("stop", help="request graceful termination of the active GUI")
    sub.add_parser("report", help="print a concise artifact handoff")

    hidden = sub.add_parser("_supervise", help=argparse.SUPPRESS)
    hidden.add_argument("--runtime-root", required=True)
    hidden.add_argument("--session", required=True)
    hidden.add_argument("--cwd", required=True)
    hidden.add_argument("--interval", type=float, default=1.0)
    hidden.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    if args.action == "launch":
        return _launch(args)
    if args.action == "status":
        return _status(args)
    if args.action == "snapshot":
        return _snapshot(args)
    if args.action == "stop":
        return _stop(args)
    if args.action == "report":
        return _report(args)
    if args.action == "_supervise":
        command = list(args.command)
        if command and command[0] == "--":
            command = command[1:]
        args.command = command
        return _supervise(args)
    _parser().print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
