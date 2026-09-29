"""Scanner adapter protocol and Finding model."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol


@dataclass
class Finding:
    source: str
    severity: str
    type: str
    rule: str
    message: str
    file: str
    line: Any = "-"
    key: str | None = None
    status: str = "OPEN"
    resolution: Any = None
    url: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_issue_dict(self) -> dict[str, Any]:
        data = asdict(self)
        extra = data.pop("extra", {}) or {}
        # Flatten known extras only into message side-channel; keep schema stable.
        if extra:
            data["extra"] = extra
        return data


class Scanner(Protocol):
    name: str

    def run(
        self,
        workspace: Path,
        config: Mapping[str, Any],
        *,
        context: Mapping[str, Any] | None = None,
    ) -> list[Finding]:
        """Execute the scanner and return normalized findings."""
