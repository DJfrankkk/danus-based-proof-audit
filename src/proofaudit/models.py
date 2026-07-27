from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """Raised when audit configuration is invalid."""


@dataclass(frozen=True)
class ReviewerSpec:
    id: str
    name: str
    lens: str
    required: bool = True


@dataclass(frozen=True)
class GateConfig:
    policy: str = "all"
    min_passes: int = 0
    block_on_fail: bool = True
    block_on_uncertain: bool = True


@dataclass(frozen=True)
class RunnerConfig:
    kind: str = "manual"
    executable: str = "codex"
    model: str = ""
    reasoning_effort: str = "high"
    timeout_seconds: int = 1800
    parallel: bool = True


@dataclass(frozen=True)
class ItemSpec:
    id: str
    title: str
    unit: str
    start: int
    end: int


@dataclass(frozen=True)
class AuditConfig:
    root: Path
    name: str
    source: str
    source_kind: str
    mode: str
    runner: RunnerConfig
    gate: GateConfig
    reviewers: tuple[ReviewerSpec, ...]
    items: tuple[ItemSpec, ...]
    version: int = 1

    @property
    def source_path(self) -> Path:
        return (self.root / self.source).resolve()

    @property
    def required_reviewers(self) -> tuple[ReviewerSpec, ...]:
        return tuple(reviewer for reviewer in self.reviewers if reviewer.required)


@dataclass
class SourceDocument:
    path: Path
    kind: str
    units: list[str]
    unit_name: str

    def extract(self, item: ItemSpec) -> str:
        if item.unit != self.unit_name:
            raise ConfigError(
                f"item {item.id} uses {item.unit!r}, but source uses {self.unit_name!r}"
            )
        if item.start < 1 or item.end < item.start or item.end > len(self.units):
            raise ConfigError(
                f"item {item.id} range {item.start}-{item.end} is outside "
                f"1-{len(self.units)} {self.unit_name}"
            )
        selected = self.units[item.start - 1 : item.end]
        if self.unit_name == "pages":
            return "\n\n".join(
                f"[PDF page {item.start + offset}]\n{text}"
                for offset, text in enumerate(selected)
            )
        return "\n".join(selected)


def public_state(state: dict[str, Any]) -> dict[str, Any]:
    """Return the JSON-safe portion exposed by reports and the dashboard."""
    return {
        "version": state.get("version", 1),
        "project_name": state.get("project_name", ""),
        "mode": state.get("mode", ""),
        "source": state.get("source", {}),
        "gate": state.get("gate", {}),
        "reviewers": state.get("reviewers", []),
        "items": state.get("items", []),
        "created_at": state.get("created_at"),
        "updated_at": state.get("updated_at"),
    }

