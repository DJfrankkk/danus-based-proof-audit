from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from .models import (
    AuditConfig,
    ConfigError,
    GateConfig,
    ItemSpec,
    ReviewerSpec,
    RunnerConfig,
)


REVIEWER_PRESETS: dict[str, tuple[ReviewerSpec, ...]] = {
    "quick": (
        ReviewerSpec(
            "logic",
            "Logic and statement",
            "Check the exact claim, definitions, typing, quantifiers, and every inference.",
        ),
        ReviewerSpec(
            "adversarial",
            "Adversarial",
            "Try to refute the claim, remove hidden assumptions, and construct boundary cases.",
        ),
    ),
    "strict": (
        ReviewerSpec(
            "logic",
            "Logic and statement",
            "Check the exact claim, definitions, typing, quantifiers, and every inference.",
        ),
        ReviewerSpec(
            "foundations",
            "Foundations and existence",
            "Track ambient structures, existence of objects, absoluteness, and imported results.",
        ),
        ReviewerSpec(
            "adversarial",
            "Adversarial",
            "Try to refute the claim, weaken hypotheses, and expose hidden assumptions.",
        ),
    ),
}

_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_ITEM_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{field} must be a non-empty string")
    return value.strip()


def load_config(project: str | Path) -> AuditConfig:
    root = Path(project).resolve()
    path = root / "audit.toml"
    if not path.is_file():
        raise ConfigError(f"audit config not found: {path}")
    with path.open("rb") as handle:
        raw = tomllib.load(handle)

    version = int(raw.get("version", 1))
    project_raw = raw.get("project")
    if not isinstance(project_raw, dict):
        raise ConfigError("[project] is required")
    name = _string(project_raw.get("name"), "project.name")
    source = _string(project_raw.get("source"), "project.source")
    source_path = (root / source).resolve()
    try:
        source_path.relative_to(root)
    except ValueError as exc:
        raise ConfigError("project.source must stay inside the audit project") from exc
    source_kind = _string(project_raw.get("source_kind"), "project.source_kind").lower()
    mode = str(project_raw.get("mode", "custom"))

    runner_raw = raw.get("runner", {})
    runner = RunnerConfig(
        kind=str(runner_raw.get("kind", "manual")).lower(),
        executable=str(runner_raw.get("executable", "codex")),
        model=str(runner_raw.get("model", "")),
        reasoning_effort=str(runner_raw.get("reasoning_effort", "high")),
        timeout_seconds=int(runner_raw.get("timeout_seconds", 1800)),
        parallel=bool(runner_raw.get("parallel", True)),
    )
    if runner.kind not in {"manual", "mock", "codex"}:
        raise ConfigError("runner.kind must be manual, mock, or codex")
    if runner.timeout_seconds < 1:
        raise ConfigError("runner.timeout_seconds must be positive")

    reviewers_raw = raw.get("reviewers")
    if not isinstance(reviewers_raw, list) or not reviewers_raw:
        raise ConfigError("at least one [[reviewers]] entry is required")
    reviewers: list[ReviewerSpec] = []
    reviewer_ids: set[str] = set()
    for index, entry in enumerate(reviewers_raw, 1):
        if not isinstance(entry, dict):
            raise ConfigError(f"reviewers entry {index} must be a table")
        reviewer_id = _string(entry.get("id"), f"reviewers[{index}].id")
        if not _ID_RE.match(reviewer_id):
            raise ConfigError(f"invalid reviewer id: {reviewer_id}")
        if reviewer_id in reviewer_ids:
            raise ConfigError(f"duplicate reviewer id: {reviewer_id}")
        reviewer_ids.add(reviewer_id)
        reviewers.append(
            ReviewerSpec(
                reviewer_id,
                str(entry.get("name", reviewer_id)).strip() or reviewer_id,
                _string(entry.get("lens"), f"reviewers[{index}].lens"),
                bool(entry.get("required", True)),
            )
        )
    if not any(reviewer.required for reviewer in reviewers):
        raise ConfigError("at least one reviewer must be required")

    gate_raw = raw.get("gate", {})
    gate = GateConfig(
        policy=str(gate_raw.get("policy", "all")).lower(),
        min_passes=int(gate_raw.get("min_passes", 0)),
        block_on_fail=bool(gate_raw.get("block_on_fail", True)),
        block_on_uncertain=bool(gate_raw.get("block_on_uncertain", True)),
    )
    if gate.policy not in {"all", "quorum"}:
        raise ConfigError("gate.policy must be all or quorum")
    required_count = sum(1 for reviewer in reviewers if reviewer.required)
    if gate.policy == "quorum":
        if gate.min_passes < 1 or gate.min_passes > required_count:
            raise ConfigError(
                f"gate.min_passes must be between 1 and {required_count} for quorum"
            )

    items_raw = raw.get("items")
    if not isinstance(items_raw, list) or not items_raw:
        raise ConfigError("at least one [[items]] entry is required")
    items: list[ItemSpec] = []
    item_ids: set[str] = set()
    for index, entry in enumerate(items_raw, 1):
        if not isinstance(entry, dict):
            raise ConfigError(f"items entry {index} must be a table")
        item_id = _string(entry.get("id"), f"items[{index}].id")
        if not _ITEM_ID_RE.match(item_id):
            raise ConfigError(
                f"invalid item id {item_id!r}; use letters, digits, dots, dashes, or underscores"
            )
        if item_id in item_ids:
            raise ConfigError(f"duplicate item id: {item_id}")
        item_ids.add(item_id)
        unit = str(entry.get("unit", "lines")).lower()
        if unit not in {"lines", "pages"}:
            raise ConfigError(f"item {item_id}: unit must be lines or pages")
        start = int(entry.get("start", 0))
        end = int(entry.get("end", 0))
        if start < 1 or end < start:
            raise ConfigError(f"item {item_id}: invalid range {start}-{end}")
        items.append(
            ItemSpec(
                item_id,
                _string(entry.get("title"), f"items[{index}].title"),
                unit,
                start,
                end,
            )
        )

    return AuditConfig(
        root=root,
        name=name,
        source=source,
        source_kind=source_kind,
        mode=mode,
        runner=runner,
        gate=gate,
        reviewers=tuple(reviewers),
        items=tuple(items),
        version=version,
    )


def _quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def render_config(config: AuditConfig) -> str:
    lines = [
        f"version = {config.version}",
        "",
        "[project]",
        f"name = {_quote(config.name)}",
        f"source = {_quote(config.source.replace(chr(92), '/'))}",
        f"source_kind = {_quote(config.source_kind)}",
        f"mode = {_quote(config.mode)}",
        "",
        "[runner]",
        f"kind = {_quote(config.runner.kind)}",
        f"executable = {_quote(config.runner.executable)}",
        f"model = {_quote(config.runner.model)}",
        f"reasoning_effort = {_quote(config.runner.reasoning_effort)}",
        f"timeout_seconds = {config.runner.timeout_seconds}",
        f"parallel = {str(config.runner.parallel).lower()}",
        "",
        "[gate]",
        f"policy = {_quote(config.gate.policy)}",
        f"min_passes = {config.gate.min_passes}",
        f"block_on_fail = {str(config.gate.block_on_fail).lower()}",
        f"block_on_uncertain = {str(config.gate.block_on_uncertain).lower()}",
    ]
    for reviewer in config.reviewers:
        lines.extend(
            [
                "",
                "[[reviewers]]",
                f"id = {_quote(reviewer.id)}",
                f"name = {_quote(reviewer.name)}",
                f"lens = {_quote(reviewer.lens)}",
                f"required = {str(reviewer.required).lower()}",
            ]
        )
    for item in config.items:
        lines.extend(
            [
                "",
                "[[items]]",
                f"id = {_quote(item.id)}",
                f"title = {_quote(item.title)}",
                f"unit = {_quote(item.unit)}",
                f"start = {item.start}",
                f"end = {item.end}",
            ]
        )
    return "\n".join(lines) + "\n"


def write_config(config: AuditConfig) -> Path:
    path = config.root / "audit.toml"
    path.write_text(render_config(config), encoding="utf-8")
    return path


def parse_custom_reviewers(values: list[str]) -> tuple[ReviewerSpec, ...]:
    reviewers: list[ReviewerSpec] = []
    for value in values:
        if "=" not in value:
            raise ConfigError("--reviewer must use ID=review lens")
        reviewer_id, lens = value.split("=", 1)
        reviewer_id = reviewer_id.strip()
        lens = lens.strip()
        if not _ID_RE.match(reviewer_id) or not lens:
            raise ConfigError(f"invalid reviewer specification: {value!r}")
        reviewers.append(ReviewerSpec(reviewer_id, reviewer_id.replace("-", " ").title(), lens))
    if len({reviewer.id for reviewer in reviewers}) != len(reviewers):
        raise ConfigError("custom reviewer ids must be unique")
    return tuple(reviewers)
