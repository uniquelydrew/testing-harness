from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


EVIDENCE_ROLES = frozenset({"expected", "actual", "comparison", "diagnostic"})
_MISSING = object()


@dataclass(frozen=True)
class EvidenceItem:
    """One typed piece of assertion evidence.

    ``role`` describes how the evidence participates in the assertion while
    ``type`` describes how a report should render it.  Multiple evidence items
    may share a role; for example an existence assertion can retain both the
    observed component state and an actual component screenshot.
    """

    role: str
    type: str
    value: Any = _MISSING
    path: str | None = None
    description: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.role not in EVIDENCE_ROLES:
            raise ValueError(
                "invalid evidence role %r; expected one of: %s"
                % (self.role, ", ".join(sorted(EVIDENCE_ROLES)))
            )
        if not isinstance(self.type, str) or not self.type.strip():
            raise ValueError("evidence type must be a non-empty string")
        if self.path is not None:
            candidate = Path(self.path)
            if candidate.is_absolute() or ".." in candidate.parts:
                raise ValueError("evidence artifact paths must be relative to the run directory")

    @classmethod
    def value_item(
        cls,
        role: str,
        evidence_type: str,
        value: Any,
        *,
        description: str = "",
        metadata: Mapping[str, Any] | None = None,
    ) -> "EvidenceItem":
        return cls(
            role=role,
            type=evidence_type,
            value=value,
            description=description,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def artifact(
        cls,
        role: str,
        evidence_type: str,
        path: str | Path,
        *,
        description: str = "",
        metadata: Mapping[str, Any] | None = None,
    ) -> "EvidenceItem":
        return cls(
            role=role,
            type=evidence_type,
            path=Path(path).as_posix(),
            description=description,
            metadata=dict(metadata or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": self.role, "type": self.type}
        if self.value is not _MISSING:
            payload["value"] = self.value
        if self.path is not None:
            payload["path"] = self.path
        if self.description:
            payload["description"] = self.description
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload
