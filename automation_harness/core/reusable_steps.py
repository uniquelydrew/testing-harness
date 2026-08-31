"""Persistence for explicitly user-authored reusable test compositions."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from automation_harness.core.test_plan import load_plan, save_plan
from automation_harness.models.plan import TestPlan


class ReusableStepError(ValueError):
    pass


@dataclass(frozen=True)
class ReusableStepDefinition:
    step_id: str
    name: str
    description: str
    plan: TestPlan
    inputs: Mapping[str, Mapping[str, Any]]
    outputs: Mapping[str, str]

    def save(self, directory: Path) -> Path:
        _validate_id(self.step_id)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (self.step_id.replace(".", "_") + ".yaml")
        plan_document = yaml.safe_load(_plan_yaml(self.plan))
        document = {
            "version": 1,
            "id": self.step_id,
            "name": self.name,
            "description": self.description,
            "inputs": dict(self.inputs),
            "outputs": dict(self.outputs),
            "plan": plan_document,
        }
        path.write_text(yaml.safe_dump(document, sort_keys=False, allow_unicode=True), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> "ReusableStepDefinition":
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, Mapping) or raw.get("version") != 1:
            raise ReusableStepError("invalid reusable-step document: %s" % path)
        step_id = raw.get("id"); name = raw.get("name")
        if not isinstance(step_id, str) or not isinstance(name, str):
            raise ReusableStepError("reusable step requires string id and name")
        _validate_id(step_id)
        plan_raw = raw.get("plan")
        if not isinstance(plan_raw, Mapping):
            raise ReusableStepError("reusable step requires a plan mapping")
        temporary = path.parent / ("." + path.name + ".plan.tmp")
        try:
            temporary.write_text(yaml.safe_dump(dict(plan_raw), sort_keys=False), encoding="utf-8")
            plan = load_plan(temporary)
        finally:
            try: temporary.unlink()
            except OSError: pass
        inputs = raw.get("inputs", {}); outputs = raw.get("outputs", {})
        if not isinstance(inputs, Mapping) or not isinstance(outputs, Mapping):
            raise ReusableStepError("reusable step inputs and outputs must be mappings")
        return cls(step_id, name, str(raw.get("description", "")), plan, dict(inputs), dict(outputs))


def list_reusable_steps(directory: Path) -> tuple[ReusableStepDefinition, ...]:
    if not directory.is_dir():
        return ()
    return tuple(ReusableStepDefinition.load(path) for path in sorted(directory.glob("*.yaml")))


def _validate_id(value: str) -> None:
    if not value or any(not (part.replace("-", "_").isalnum()) for part in value.split(".")):
        raise ReusableStepError("invalid reusable step id %r" % value)


def _plan_yaml(plan: TestPlan) -> str:
    from tempfile import NamedTemporaryFile
    with NamedTemporaryFile("w+", suffix=".yaml", delete=False) as handle:
        path = Path(handle.name)
    try:
        save_plan(plan, path)
        return path.read_text(encoding="utf-8")
    finally:
        try: path.unlink()
        except OSError: pass
