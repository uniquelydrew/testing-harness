"""Runtime evidence for promotion from a physical hit node to a semantic object."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from automation_harness.models.component import CapturedComponent


@dataclass(frozen=True)
class SemanticTargetResolution:
    physical_target: CapturedComponent
    semantic_target: CapturedComponent
    promoted: bool
    relationship: Mapping[str, Any]

    def capture(self) -> CapturedComponent:
        """Return the authoring capture with compact physical-node evidence."""
        if not self.promoted:
            return self.semantic_target
        return dataclass_replace_properties(self.semantic_target, {
            "physical_target": {
                "class": self.physical_target.native_class,
                "text": self.physical_target.name,
                "ref": self.physical_target.backend_properties.get("ref"),
                **dict(self.relationship),
            },
        })


def dataclass_replace_properties(capture: CapturedComponent, extra: Mapping[str, Any]) -> CapturedComponent:
    from dataclasses import replace
    return replace(capture, backend_properties={**dict(capture.backend_properties), **dict(extra)})
