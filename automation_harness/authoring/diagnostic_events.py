"""Lightweight, non-fatal authoring diagnostics for supervised sessions.

Python 3.6 compatible; never writes full plan or repository contents.
"""
import datetime
import hashlib
import json
import os
import threading


def artifact_fingerprint(path):
    if not path or not os.path.isfile(str(path)):
        return {"exists": False}
    digest = hashlib.sha256()
    with open(str(path), "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"exists": True, "sha256": digest.hexdigest(), "bytes": os.path.getsize(str(path))}


def emit(event, **fields):
    """Append a diagnostic event when launched by codex-runtime.

    Instrumentation must not prevent opening or migrating a plan.
    """
    session = os.environ.get("AUTOMATION_HARNESS_CODEX_SESSION_DIR")
    if not session:
        return
    record = {
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "event": str(event),
        "pid": os.getpid(),
        "thread": threading.current_thread().name,
        "fields": fields,
    }
    try:
        path = os.path.join(session, "authoring-events.jsonl")
        with open(path, "a") as stream:
            stream.write(json.dumps(record, sort_keys=True, default=str) + "\n")
    except (IOError, OSError, TypeError, ValueError):
        pass
