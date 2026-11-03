"""Read-only, GUI-independent inspection of persisted test execution evidence.

The HTML report is an export. JSON artifacts remain the source of truth for GTK views.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class ReportStep:
    node_id: str
    step_id: str
    name: str
    status: str
    error: str = ""
    assertions: tuple[dict, ...] = ()


@dataclass(frozen=True)
class RunReport:
    run_id: str
    plan_name: str
    status: str
    started_at: str
    finished_at: str | None
    passed: int
    failed: int
    skipped: int
    artifact_dir: Path | None
    source_plan_path: Path | None
    steps: tuple[ReportStep, ...]
    diagnostics: tuple[str, ...] = ()
    legacy_identity: bool = False

    @property
    def not_run(self) -> int:
        return sum(step.status == "NOT RUN" for step in self.steps)

    @property
    def report_html(self) -> Path | None:
        if self.artifact_dir is None:
            return None
        path = self.artifact_dir / "report.html"
        return path if path.is_file() else None


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _status(raw: str) -> str:
    normalized = str(raw or "").strip().lower().replace("_", " ")
    if normalized in ("passed", "pass", "success"):
        return "PASSED"
    if normalized in ("failed", "fail", "error"):
        return "FAILED"
    if normalized == "running":
        return "RUNNING"
    if normalized in ("cancelled", "canceled"):
        return "CANCELLED"
    if normalized == "skipped":
        return "SKIPPED"
    return "NOT RUN"


def _steps(directory: Path) -> tuple[ReportStep, ...]:
    state = _read_json(directory / "execution_state.json")
    labels = _read_json(directory / "step_labels.json")
    persisted_steps = state.get("steps", {})
    if not isinstance(persisted_steps, dict):
        persisted_steps = {}

    by_node = {}
    order = []

    def ensure(node_id):
        if not isinstance(node_id, str) or not node_id:
            return None
        if node_id not in by_node:
            by_node[node_id] = {
                "node_id": node_id, "step_id": "", "status": "NOT RUN",
                "error": "", "assertions": [],
            }
            order.append(node_id)
        return by_node[node_id]

    events = directory / "events.jsonl"
    if events.is_file():
        try:
            with events.open(encoding="utf-8") as source:
                for line in source:
                    try:
                        event = json.loads(line)
                    except (TypeError, ValueError):
                        continue
                    if not isinstance(event, dict):
                        continue
                    kind = event.get("event")
                    if kind not in ("plan_step_started", "plan_step_finished", "plan_step_failed", "assertion"):
                        continue
                    item = ensure(event.get("node_id"))
                    if item is None:
                        continue
                    if kind == "plan_step_started":
                        item["status"] = "RUNNING"
                        item["step_id"] = str(event.get("step_id") or event.get("step") or item["step_id"])
                    elif kind == "plan_step_finished":
                        item["status"] = "PASSED"
                    elif kind == "plan_step_failed":
                        item["status"] = "FAILED"
                        item["error"] = str(event.get("error") or "")
                        item["step_id"] = str(event.get("step_id") or event.get("step") or item["step_id"])
                    else:
                        item["assertions"].append(event)
        except OSError:
            pass

    for node_id, raw in persisted_steps.items():
        item = ensure(node_id)
        if item is None or not isinstance(raw, dict):
            continue
        # The final execution state is authoritative; events establish timeline order.
        item["status"] = _status(raw.get("status"))
        item["step_id"] = str(raw.get("step_id") or item["step_id"])
        item["error"] = str(raw.get("error") or item["error"])

    results = []
    for node_id in order:
        item = by_node[node_id]
        label = labels.get(node_id, {})
        if isinstance(label, str):
            label = {"name": label}
        if not isinstance(label, dict):
            label = {}
        name = str(label.get("name") or item["step_id"] or node_id)
        results.append(ReportStep(
            node_id=node_id,
            step_id=item["step_id"] or str(label.get("step_id") or ""),
            name=name,
            status=item["status"],
            error=item["error"],
            assertions=tuple(item["assertions"]),
        ))
    return tuple(results)


def load_run_report(artifact_dir: Path, *, fallback_record=None) -> RunReport | None:
    """Load one run, including legacy runs and project-record-only failures."""
    directory = Path(artifact_dir) if artifact_dir is not None else None
    data = _read_json(directory / "run.json") if directory is not None else {}
    if not data and fallback_record is None:
        return None
    record = fallback_record

    def field(key, default=None):
        value = data.get(key)
        if value is not None:
            return value
        return getattr(record, key, default) if record is not None else default

    name = str(data.get("bundle") or field("plan_name") or "")
    source_text = field("source_plan_path") or field("plan_path")
    source = Path(source_text).resolve() if source_text else None
    status = field("status")
    exit_code = field("exit_code")
    if not status:
        status = "running" if exit_code is None else ("passed" if exit_code == 0 else "failed")
    diagnostics = data.get("validation_errors") or []
    if not isinstance(diagnostics, list):
        diagnostics = []
    if record is not None and getattr(record, "error", None):
        diagnostics.append(str(record.error))
    return RunReport(
        run_id=str(field("run_id") or (directory.name if directory else "")),
        plan_name=name,
        status=_status(status),
        started_at=str(field("started_at") or ""),
        finished_at=field("finished_at"),
        passed=int(field("passed", 0)),
        failed=int(field("failed", 0)),
        skipped=int(field("skipped", 0)),
        artifact_dir=directory if directory is not None and directory.is_dir() else None,
        source_plan_path=source,
        steps=_steps(directory) if directory is not None and directory.is_dir() else (),
        diagnostics=tuple(str(line) for line in diagnostics),
        legacy_identity=source is None,
    )


def list_plan_reports(runs_dir: Path, plan_path: Path, plan_name: str) -> tuple[RunReport, ...]:
    """Discover standalone and batched runs, without relying on artifact timestamps.

    Modern runs match their canonical source path. Older runs can only be matched
    by bundle name and are explicitly flagged as ambiguous in the viewer.
    """
    base = Path(runs_dir)
    if not base.is_dir():
        return ()
    canonical = Path(plan_path).resolve()
    reports = []
    for metadata in base.glob("*/run.json"):
        report = load_run_report(metadata.parent)
        if report is None:
            continue
        if report.source_plan_path == canonical or (
            report.source_plan_path is None and report.plan_name == plan_name
        ):
            reports.append(report)
    return tuple(sorted(reports, key=lambda item: (item.started_at, item.run_id), reverse=True))
