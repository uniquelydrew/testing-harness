from __future__ import annotations

import html
import json
from collections import OrderedDict
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import quote


def render_html_report(path: Path, events_path: Path, plan, result) -> None:
    """Render a tester-oriented HTML report from authoritative run evidence."""
    events = _read_events(events_path)
    step_events = _step_events(events)
    assertions = _assertions_by_node(events)
    groups = _group_steps(plan.steps)
    status = "PASS" if result.exit_code == 0 else "FAIL"
    failed_assertions = sum(
        1 for event in events if event.get("event") == "assertion" and event.get("passed") is False
    )

    body = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>%s — Automation Harness</title>" % html.escape(str(plan.name)),
        "<style>%s</style></head><body>" % _CSS,
        '<main class="report">',
        '<header class="run-header %s">' % ("pass" if status == "PASS" else "fail"),
        '<div><div class="eyebrow">Automation Harness</div><h1>%s</h1></div>' % html.escape(str(plan.name)),
        '<div class="run-status">%s</div>' % status,
        "</header>",
        '<section class="summary-grid">',
        _summary_card("Backend", getattr(result, "backend", "unknown")),
        _summary_card("Passed steps", getattr(result, "passed", 0)),
        _summary_card("Failed steps", getattr(result, "failed", 0)),
        _summary_card("Failed assertions", failed_assertions),
        _summary_card("Run ID", getattr(result, "run_id", "")),
        _summary_card("Exit code", getattr(result, "exit_code", "")),
        "</section>",
    ]

    errors = list(getattr(result, "validation_errors", ()) or ())
    if errors:
        body.append('<section class="run-errors"><h2>Run errors</h2><ul>')
        body.extend("<li>%s</li>" % html.escape(str(item)) for item in errors)
        body.append("</ul></section>")

    body.append('<section class="execution"><h2>Execution</h2>')
    if not groups:
        body.append('<p class="muted">No executable steps were authored.</p>')
    for group_name, calls in groups.items():
        body.append('<section class="step-group"><h3>%s</h3>' % html.escape(group_name))
        for call in calls:
            event = step_events.get(call.node_id, {})
            state = event.get("status", "not-run")
            failed = state == "failed"
            details_attr = " open" if failed else ""
            body.append('<details class="step-card %s"%s>' % (html.escape(state), details_attr))
            body.append(
                '<summary><span class="step-status">%s</span><span class="step-title">%s</span>'
                '<code>%s</code></summary>'
                % (
                    html.escape(state.upper()),
                    html.escape(call.node_id),
                    html.escape(call.step_id),
                )
            )
            body.append('<div class="step-body">')
            if event.get("error"):
                body.append('<div class="failure-message">%s</div>' % html.escape(str(event["error"])))
            for assertion in assertions.get(call.node_id, ()):
                body.append(_render_assertion(assertion))
            if not assertions.get(call.node_id):
                body.append('<p class="muted">No assertion evidence recorded for this action.</p>')
            body.append("</div></details>")
        body.append("</section>")

    unscoped = assertions.get(None, ())
    if unscoped:
        body.append('<section class="step-group"><h3>Unscoped assertions</h3>')
        body.extend(_render_assertion(event) for event in unscoped)
        body.append("</section>")

    body.extend(
        [
            "</section>",
            '<footer>Generated from <code>events.jsonl</code>; structured evidence remains authoritative.</footer>',
            "</main></body></html>",
        ]
    )
    path.write_text("\n".join(body) + "\n", encoding="utf-8")


def _read_events(path: Path) -> list[dict[str, Any]]:
    events = []
    if not path.is_file():
        return events
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
        except ValueError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def _step_events(events: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for event in events:
        node_id = event.get("node_id")
        if not isinstance(node_id, str):
            continue
        if event.get("event") == "plan_step_started":
            result.setdefault(node_id, {})["status"] = "running"
        elif event.get("event") == "plan_step_finished":
            result.setdefault(node_id, {})["status"] = "passed"
        elif event.get("event") == "plan_step_failed":
            item = result.setdefault(node_id, {})
            item["status"] = "failed"
            item["error"] = event.get("error")
    return result


def _assertions_by_node(events: Iterable[Mapping[str, Any]]) -> dict[str | None, list[Mapping[str, Any]]]:
    result: dict[str | None, list[Mapping[str, Any]]] = {}
    for event in events:
        if event.get("event") != "assertion":
            continue
        node_id = event.get("node_id")
        key = node_id if isinstance(node_id, str) else None
        result.setdefault(key, []).append(event)
    return result


def _group_steps(calls) -> "OrderedDict[str, list[Any]]":
    groups: "OrderedDict[str, list[Any]]" = OrderedDict()
    for call in calls:
        group = (call.group or "Ungrouped").strip() or "Ungrouped"
        groups.setdefault(group, []).append(call)
    return groups


def _render_assertion(assertion: Mapping[str, Any]) -> str:
    passed = bool(assertion.get("passed"))
    css = "pass" if passed else "fail"
    title = str(assertion.get("assertion") or "assertion").replace("_", " ").title()
    component = assertion.get("component_id")
    message = assertion.get("message")
    evidence = assertion.get("evidence")
    if not isinstance(evidence, list):
        evidence = [
            {"role": "expected", "type": "value", "value": assertion.get("expected")},
            {"role": "actual", "type": "value", "value": assertion.get("actual")},
        ]
    role_groups = {role: [] for role in ("expected", "actual", "comparison", "diagnostic")}
    for item in evidence:
        if isinstance(item, Mapping):
            role_groups.setdefault(str(item.get("role", "diagnostic")), []).append(item)

    parts = [
        '<article class="assertion-card %s">' % css,
        '<div class="assertion-heading"><span class="assertion-status">%s</span><h4>%s</h4></div>'
        % ("PASS" if passed else "FAIL", html.escape(title)),
    ]
    if component:
        parts.append('<div class="object-id">Object: <code>%s</code></div>' % html.escape(str(component)))
    if message:
        parts.append('<div class="assertion-message">%s</div>' % html.escape(str(message)))

    visible_roles = [role for role in ("expected", "actual", "comparison") if role_groups.get(role)]
    if visible_roles:
        parts.append('<div class="evidence-grid columns-%d">' % len(visible_roles))
        for role in visible_roles:
            parts.append('<section class="evidence-column"><h5>%s</h5>' % html.escape(role.title()))
            parts.extend(_render_evidence_item(item) for item in role_groups[role])
            parts.append("</section>")
        parts.append("</div>")
    if role_groups.get("diagnostic"):
        parts.append('<details class="diagnostics"%s><summary>Diagnostics</summary>' % (" open" if not passed else ""))
        parts.extend(_render_evidence_item(item) for item in role_groups["diagnostic"])
        parts.append("</details>")
    parts.append("</article>")
    return "\n".join(parts)


def _render_evidence_item(item: Mapping[str, Any]) -> str:
    evidence_type = str(item.get("type") or "value")
    description = item.get("description")
    metadata = item.get("metadata")
    parts = ['<div class="evidence-item">']
    if description:
        parts.append('<div class="evidence-description">%s</div>' % html.escape(str(description)))
    path = item.get("path")
    if isinstance(path, str) and path:
        safe_path = quote(path, safe="/._-")
        if evidence_type in {"image", "image_diff"}:
            parts.append(
                '<a href="%s"><img loading="lazy" src="%s" alt="%s evidence"></a>'
                % (safe_path, safe_path, html.escape(evidence_type))
            )
        else:
            parts.append('<a class="artifact-link" href="%s">%s</a>' % (safe_path, html.escape(path)))
    if "value" in item:
        parts.append("<pre>%s</pre>" % html.escape(json.dumps(item.get("value"), indent=2, sort_keys=True, default=str)))
    if isinstance(metadata, Mapping) and metadata:
        parts.append('<details class="metadata"><summary>Metadata</summary><pre>%s</pre></details>' % html.escape(
            json.dumps(dict(metadata), indent=2, sort_keys=True, default=str)
        ))
    parts.append("</div>")
    return "\n".join(parts)


def _summary_card(label: str, value: Any) -> str:
    return '<div class="summary-card"><span>%s</span><strong>%s</strong></div>' % (
        html.escape(str(label)), html.escape(str(value)),
    )


_CSS = r"""
:root { color-scheme: light dark; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
* { box-sizing: border-box; }
body { margin: 0; background: #f4f6f8; color: #18202a; }
.report { max-width: 1280px; margin: 0 auto; padding: 32px; }
.run-header { display: flex; justify-content: space-between; align-items: center; gap: 24px; border-radius: 16px; padding: 24px 28px; color: white; background: #25313d; }
.run-header.pass { background: #17663a; }
.run-header.fail { background: #9f2f2f; }
.eyebrow { font-size: 12px; font-weight: 700; letter-spacing: .12em; text-transform: uppercase; opacity: .8; }
h1 { margin: 4px 0 0; font-size: 28px; }
.run-status { font-size: 32px; font-weight: 800; }
.summary-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin: 18px 0 28px; }
.summary-card { background: white; border: 1px solid #dfe4ea; border-radius: 10px; padding: 14px 16px; min-width: 0; }
.summary-card span { display: block; color: #66717e; font-size: 12px; margin-bottom: 5px; }
.summary-card strong { display: block; overflow-wrap: anywhere; }
h2 { margin: 28px 0 14px; }
.step-group { margin: 22px 0; }
.step-group h3 { font-size: 16px; color: #4b5663; }
.step-card { background: white; border: 1px solid #dfe4ea; border-left: 5px solid #8b96a3; border-radius: 10px; margin: 10px 0; overflow: hidden; }
.step-card.passed { border-left-color: #23834d; }
.step-card.failed { border-left-color: #c43f3f; }
.step-card summary { cursor: pointer; display: grid; grid-template-columns: 76px minmax(180px, 1fr) minmax(220px, auto); align-items: center; gap: 12px; padding: 14px 16px; }
.step-status { font-size: 12px; font-weight: 800; }
.step-title { font-weight: 700; }
.step-card code, .object-id code { overflow-wrap: anywhere; }
.step-body { border-top: 1px solid #e6eaee; padding: 16px; }
.failure-message, .assertion-message, .run-errors { background: #fff0f0; border: 1px solid #f0b8b8; border-radius: 8px; padding: 12px; margin: 10px 0; }
.assertion-card { border: 1px solid #dfe4ea; border-radius: 10px; padding: 16px; margin: 10px 0; }
.assertion-card.pass { background: #f6fcf8; }
.assertion-card.fail { background: #fff8f8; border-color: #e9b1b1; }
.assertion-heading { display: flex; align-items: center; gap: 10px; }
.assertion-heading h4 { margin: 0; font-size: 16px; }
.assertion-status { font-size: 11px; font-weight: 900; padding: 4px 7px; border-radius: 999px; background: #dce3e9; }
.assertion-card.pass .assertion-status { background: #ccebd8; color: #145f35; }
.assertion-card.fail .assertion-status { background: #f4cccc; color: #8e2424; }
.object-id { margin-top: 8px; color: #596572; font-size: 13px; }
.evidence-grid { display: grid; gap: 12px; margin-top: 14px; align-items: start; }
.evidence-grid.columns-1 { grid-template-columns: 1fr; }
.evidence-grid.columns-2 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.evidence-grid.columns-3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.evidence-column { min-width: 0; background: white; border: 1px solid #e1e6eb; border-radius: 8px; padding: 12px; }
.evidence-column h5 { margin: 0 0 9px; font-size: 12px; letter-spacing: .05em; text-transform: uppercase; color: #596572; }
.evidence-item + .evidence-item { border-top: 1px solid #e7ebef; margin-top: 12px; padding-top: 12px; }
.evidence-description { font-size: 13px; margin-bottom: 8px; }
.evidence-item img { display: block; max-width: 100%; max-height: 420px; object-fit: contain; border: 1px solid #d7dde3; border-radius: 6px; background: #f8fafb; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; background: #f3f5f7; border-radius: 6px; padding: 10px; font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.diagnostics, .metadata { margin-top: 12px; }
.diagnostics summary, .metadata summary { cursor: pointer; font-weight: 700; }
.artifact-link { overflow-wrap: anywhere; }
.muted { color: #788491; }
footer { margin: 36px 0 10px; color: #6d7884; font-size: 12px; }
@media (max-width: 760px) {
  .report { padding: 16px; }
  .run-header { align-items: flex-start; flex-direction: column; }
  .step-card summary { grid-template-columns: 70px 1fr; }
  .step-card summary code { grid-column: 1 / -1; }
  .evidence-grid.columns-2, .evidence-grid.columns-3 { grid-template-columns: 1fr; }
}
@media (prefers-color-scheme: dark) {
  body { background: #101418; color: #e6ebef; }
  .summary-card, .step-card, .evidence-column { background: #171d23; border-color: #333c45; }
  .step-body { border-color: #333c45; }
  .step-group h3, .object-id, .evidence-column h5, .muted, footer { color: #aab4be; }
  .assertion-card.pass { background: #14231a; }
  .assertion-card.fail { background: #2a1717; border-color: #653333; }
  .failure-message, .assertion-message, .run-errors { background: #331b1b; border-color: #6c3737; }
  pre { background: #11161b; }
  .evidence-item img { background: #11161b; border-color: #3b454f; }
}
"""
