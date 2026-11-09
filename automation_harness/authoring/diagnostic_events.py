"""Bounded, non-fatal metadata-only authoring diagnostics for Codex runtime.

This module never logs plan bodies, locators, repository YAML or credentials.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import threading


def artifact_fingerprint(path):
    if path is None:
        return {"exists": False}
    try:
        if not os.path.isfile(str(path)):
            return {"exists": False}
        digest = hashlib.sha256()
        with open(str(path), "rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return {"exists": True, "sha256": digest.hexdigest(), "bytes": os.path.getsize(str(path))}
    except (IOError, OSError, TypeError, ValueError):
        return {"exists": False, "error": "fingerprint_unavailable"}


def emit(event, **fields):
    """Record sanitized metadata when a managed runtime session is active."""
    session = os.environ.get("AUTOMATION_HARNESS_CODEX_SESSION_DIR")
    if not session or not os.path.isdir(session):
        return
    # Caller-provided data must never silently expand the diagnostic schema.
    accepted = {"plan_fingerprint", "repository_fingerprint", "project_fingerprint",
                "legacy_count", "imported_count", "reused_count", "error_type",
                "execution_mode", "state"}
    clean = {k: v for k, v in fields.items() if k in accepted and isinstance(v, (str, int, float, bool, dict))}
    record = {
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "event": str(event)[:96],
        "pid": os.getpid(),
        "thread": threading.current_thread().name,
        "fields": clean,
    }
    try:
        with open(os.path.join(session, "authoring-events.jsonl"), "a") as stream:
            stream.write(json.dumps(record, sort_keys=True, default=str) + "\n")
    except (IOError, OSError, TypeError, ValueError):
        pass
