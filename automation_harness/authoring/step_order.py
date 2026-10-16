"""Order-preserving plan edits shared by manual and recorded authoring."""
from __future__ import annotations

from dataclasses import replace


class StepOrderError(ValueError):
    pass


def insert_calls(plan, calls, *, after_node_id=None):
    """Insert calls after an existing node; None appends to the plan."""
    calls = tuple(calls)
    if not calls:
        return plan
    existing = list(plan.steps)
    existing_ids = {call.node_id for call in existing}
    new_ids = [call.node_id for call in calls]
    if len(set(new_ids)) != len(new_ids) or existing_ids.intersection(new_ids):
        raise StepOrderError("inserted node ids must be unique")
    if after_node_id is None:
        position = len(existing)
    else:
        position = next((i + 1 for i, item in enumerate(existing) if item.node_id == after_node_id), None)
        if position is None:
            raise StepOrderError("insertion anchor is no longer present: %s" % after_node_id)
    return replace(plan, steps=tuple(existing[:position] + list(calls) + existing[position:]))


def reorder_calls(plan, ordered_node_ids):
    """Validate the proposed order before mutating the authoritative plan."""
    ordered_node_ids = tuple(ordered_node_ids)
    current = {call.node_id: call for call in plan.steps}
    if len(ordered_node_ids) != len(current) or set(ordered_node_ids) != set(current):
        raise StepOrderError("reorder must contain each node exactly once")
    positions = {node_id: index for index, node_id in enumerate(ordered_node_ids)}
    for call in plan.steps:
        for predecessor in call.depends_on:
            if predecessor in positions and positions[predecessor] > positions[call.node_id]:
                raise StepOrderError("node %s must follow dependency %s" % (call.node_id, predecessor))
    return replace(plan, steps=tuple(current[node_id] for node_id in ordered_node_ids))
