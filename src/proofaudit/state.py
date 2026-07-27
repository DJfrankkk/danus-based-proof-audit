from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .models import AuditConfig, ConfigError, SourceDocument
from .source import item_hash, sha256_file


STATE_DIR = ".proofaudit"
STATE_FILE = "state.json"
EVENTS_FILE = "events.jsonl"
LEDGER_FILE = "ledger.md"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def state_dir(root: Path) -> Path:
    return root / STATE_DIR


def state_path(root: Path) -> Path:
    return state_dir(root) / STATE_FILE


def load_state(root: Path) -> dict[str, Any]:
    path = state_path(root)
    if not path.is_file():
        raise ConfigError(f"audit state not found: {path}; run proofaudit bootstrap")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid audit state: {path}: {exc}") from exc


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def save_state(root: Path, state: dict[str, Any]) -> Path:
    state["updated_at"] = utc_now()
    path = state_path(root)
    _atomic_text(path, json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    write_ledger(root, state)
    return path


def append_event(root: Path, event_type: str, **fields: Any) -> None:
    record = {"timestamp": utc_now(), "event": event_type, **fields}
    path = state_dir(root) / EVENTS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


@contextmanager
def project_lock(root: Path) -> Iterator[None]:
    directory = state_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / "run.lock"
    descriptor: int | None = None
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ConfigError(
            f"audit project is already locked: {lock}; remove only if no run is active"
        ) from exc
    try:
        os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
        os.close(descriptor)
        descriptor = None
        yield
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            lock.unlink()
        except FileNotFoundError:
            pass


def build_state(config: AuditConfig, document: SourceDocument) -> dict[str, Any]:
    created = utc_now()
    items: list[dict[str, Any]] = []
    first_open = True
    for item in config.items:
        items.append(
            {
                "id": item.id,
                "title": item.title,
                "unit": item.unit,
                "start": item.start,
                "end": item.end,
                "content_sha256": item_hash(document, item),
                "status": "in_progress" if first_open else "pending",
                "reviews": {},
                "editorial_warnings": [],
                "closed_at": None,
                "skip_reason": "",
            }
        )
        first_open = False
    return {
        "version": 1,
        "project_name": config.name,
        "mode": config.mode,
        "source": {
            "path": config.source.replace("\\", "/"),
            "kind": config.source_kind,
            "sha256": sha256_file(config.source_path),
            "generation": 1,
        },
        "gate": {
            "policy": config.gate.policy,
            "min_passes": config.gate.min_passes,
            "block_on_fail": config.gate.block_on_fail,
            "block_on_uncertain": config.gate.block_on_uncertain,
        },
        "reviewers": [
            {
                "id": reviewer.id,
                "name": reviewer.name,
                "lens": reviewer.lens,
                "required": reviewer.required,
            }
            for reviewer in config.reviewers
        ],
        "items": items,
        "created_at": created,
        "updated_at": created,
    }


def bootstrap(config: AuditConfig, document: SourceDocument, force: bool = False) -> dict[str, Any]:
    path = state_path(config.root)
    if path.exists() and not force:
        raise ConfigError(f"audit state already exists: {path}")
    state = build_state(config, document)
    save_state(config.root, state)
    append_event(
        config.root,
        "audit_bootstrapped",
        source_sha256=state["source"]["sha256"],
        reviewer_count=len(config.reviewers),
        item_count=len(config.items),
    )
    return state


def current_item(state: dict[str, Any]) -> dict[str, Any] | None:
    for item in state.get("items", []):
        if item.get("status") not in {"closed", "skipped"}:
            return item
    return None


def _location(item: dict[str, Any]) -> str:
    return f"{item['unit']} {item['start']}-{item['end']}"


def render_ledger(state: dict[str, Any]) -> str:
    source = state["source"]
    lines = [
        f"# Audit ledger: {state['project_name']}",
        "",
        f"- Source: `{source['path']}`",
        f"- Generation: `{source['generation']}`",
        f"- SHA-256: `{source['sha256']}`",
        f"- Mode: `{state['mode']}`",
        "- Evidence level: independent AI review; this is not formal verification.",
        "",
        "| Seq. | Item | Location | Status | Reviewer evidence | Preserved notes |",
        "|---:|---|---|---|---|---|",
    ]
    for index, item in enumerate(state["items"], 1):
        evidence = []
        for role, review in sorted(item.get("reviews", {}).items()):
            verdict = review.get("mathematical_verdict", "?")
            report_hash = review.get("report_sha256", "")[:10]
            evidence.append(f"`{role}` {verdict} `{report_hash}`")
        notes = item.get("editorial_warnings", [])
        if item.get("skip_reason"):
            notes = [*notes, item["skip_reason"]]
        lines.append(
            "| "
            + " | ".join(
                [
                    f"{index:03d}",
                    item["title"].replace("|", "\\|"),
                    _location(item),
                    f"`{item['status']}`",
                    "; ".join(evidence) or "pending",
                    "<br>".join(str(note).replace("|", "\\|") for note in notes) or "",
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def write_ledger(root: Path, state: dict[str, Any]) -> Path:
    path = state_dir(root) / LEDGER_FILE
    _atomic_text(path, render_ledger(state))
    return path


def ensure_config_matches_state(config: AuditConfig, state: dict[str, Any]) -> None:
    configured = [
        (reviewer.id, reviewer.name, reviewer.lens, reviewer.required)
        for reviewer in config.reviewers
    ]
    recorded = [
        (
            reviewer["id"],
            reviewer.get("name", reviewer["id"]),
            reviewer.get("lens", ""),
            bool(reviewer.get("required", True)),
        )
        for reviewer in state.get("reviewers", [])
    ]
    configured_items = [
        (item.id, item.title, item.unit, item.start, item.end) for item in config.items
    ]
    recorded_items = [
        (item["id"], item["title"], item["unit"], item["start"], item["end"])
        for item in state.get("items", [])
    ]
    configured_gate = (
        config.gate.policy,
        config.gate.min_passes,
        config.gate.block_on_fail,
        config.gate.block_on_uncertain,
    )
    recorded_gate = (
        state.get("gate", {}).get("policy"),
        int(state.get("gate", {}).get("min_passes", 0)),
        bool(state.get("gate", {}).get("block_on_fail", True)),
        bool(state.get("gate", {}).get("block_on_uncertain", True)),
    )
    if configured != recorded or configured_items != recorded_items or configured_gate != recorded_gate:
        raise ConfigError(
            "audit.toml changed after bootstrap; run 'proofaudit refresh PROJECT' "
            "to reconcile reviewers, gate, and items"
        )


def _reset_from(items: list[dict[str, Any]], start_index: int) -> None:
    for index, item in enumerate(items):
        if index < start_index or item.get("status") == "skipped":
            continue
        item["status"] = "pending"
        item["reviews"] = {}
        item["editorial_warnings"] = []
        item["closed_at"] = None
    for item in items[start_index:]:
        if item.get("status") not in {"closed", "skipped"}:
            item["status"] = "in_progress"
            break


def refresh_state(
    config: AuditConfig, state: dict[str, Any], document: SourceDocument
) -> tuple[dict[str, Any], int | None]:
    old_items = {item["id"]: item for item in state.get("items", [])}
    old_item_order = [item["id"] for item in state.get("items", [])]
    new_items: list[dict[str, Any]] = []
    first_changed: int | None = None
    configured_reviewers = [
        (reviewer.id, reviewer.name, reviewer.lens, reviewer.required)
        for reviewer in config.reviewers
    ]
    recorded_reviewers = [
        (
            reviewer["id"],
            reviewer.get("name", reviewer["id"]),
            reviewer.get("lens", ""),
            bool(reviewer.get("required", True)),
        )
        for reviewer in state.get("reviewers", [])
    ]
    reviewer_change = configured_reviewers != recorded_reviewers

    for index, spec in enumerate(config.items):
        digest = item_hash(document, spec)
        old = old_items.get(spec.id)
        unchanged = (
            old is not None
            and index < len(old_item_order)
            and old_item_order[index] == spec.id
            and old.get("title") == spec.title
            and old.get("unit") == spec.unit
            and old.get("start") == spec.start
            and old.get("end") == spec.end
            and old.get("content_sha256") == digest
        )
        if (not unchanged or reviewer_change) and first_changed is None:
            first_changed = index
        new_items.append(
            {
                "id": spec.id,
                "title": spec.title,
                "unit": spec.unit,
                "start": spec.start,
                "end": spec.end,
                "content_sha256": digest,
                "status": old.get("status", "pending") if old else "pending",
                "reviews": old.get("reviews", {}) if old else {},
                "editorial_warnings": old.get("editorial_warnings", []) if old else [],
                "closed_at": old.get("closed_at") if old else None,
                "skip_reason": old.get("skip_reason", "") if old else "",
            }
        )

    new_source_hash = sha256_file(config.source_path)
    source_changed = new_source_hash != state.get("source", {}).get("sha256")
    config_changed = (
        reviewer_change
        or state.get("gate", {}).get("policy") != config.gate.policy
        or int(state.get("gate", {}).get("min_passes", 0)) != config.gate.min_passes
        or bool(state.get("gate", {}).get("block_on_fail", True))
        != config.gate.block_on_fail
        or bool(state.get("gate", {}).get("block_on_uncertain", True))
        != config.gate.block_on_uncertain
    )
    if config_changed and first_changed is None:
        first_changed = 0
    if first_changed is not None:
        _reset_from(new_items, first_changed)

    state["project_name"] = config.name
    state["mode"] = config.mode
    state["items"] = new_items
    state["reviewers"] = [
        {
            "id": reviewer.id,
            "name": reviewer.name,
            "lens": reviewer.lens,
            "required": reviewer.required,
        }
        for reviewer in config.reviewers
    ]
    state["gate"] = {
        "policy": config.gate.policy,
        "min_passes": config.gate.min_passes,
        "block_on_fail": config.gate.block_on_fail,
        "block_on_uncertain": config.gate.block_on_uncertain,
    }
    if source_changed:
        state["source"]["generation"] = int(state["source"].get("generation", 1)) + 1
    state["source"].update(
        {
            "path": config.source.replace("\\", "/"),
            "kind": config.source_kind,
            "sha256": new_source_hash,
        }
    )
    save_state(config.root, state)
    append_event(
        config.root,
        "audit_refreshed",
        source_changed=source_changed,
        first_invalidated_item=(
            new_items[first_changed]["id"] if first_changed is not None and new_items else None
        ),
        reviewer_count=len(config.reviewers),
    )
    return state, first_changed
