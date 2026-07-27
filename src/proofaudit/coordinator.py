from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from .config import load_config
from .models import AuditConfig, ConfigError, ItemSpec, ReviewerSpec
from .protocol import build_prompt, ensure_item, parse_review_text, validate_review
from .runner import ReviewRequest, RunnerError, make_runner
from .source import item_hash, load_document, sha256_file, sha256_text
from .state import (
    append_event,
    current_item,
    ensure_config_matches_state,
    load_state,
    project_lock,
    save_state,
    utc_now,
)


def _state_item(state: dict[str, Any], item_id: str) -> dict[str, Any]:
    for item in state["items"]:
        if item["id"] == item_id:
            return item
    raise ConfigError(f"item not found in state: {item_id}")


def _report_path(
    config: AuditConfig, generation: int, item_id: str, reviewer_id: str
) -> Path:
    return (
        config.root
        / ".proofaudit"
        / "reports"
        / f"g{generation:03d}"
        / item_id
        / f"{reviewer_id}.json"
    )


def _save_report(
    config: AuditConfig,
    state: dict[str, Any],
    item: ItemSpec,
    reviewer: ReviewerSpec,
    payload: dict[str, Any],
) -> dict[str, Any]:
    validate_review(payload)
    generated_at = utc_now()
    report = {
        "schema_version": 1,
        "project_name": config.name,
        "source_generation": state["source"]["generation"],
        "source_sha256": state["source"]["sha256"],
        "item_id": item.id,
        "item_title": item.title,
        "item_sha256": _state_item(state, item.id)["content_sha256"],
        "reviewer_id": reviewer.id,
        "reviewer_name": reviewer.name,
        "reviewer_lens": reviewer.lens,
        "generated_at": generated_at,
        **payload,
    }
    canonical = json.dumps(report, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    report_hash = sha256_text(canonical)
    report["report_sha256"] = report_hash
    path = _report_path(
        config, state["source"]["generation"], item.id, reviewer.id
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)
    report["report_path"] = str(path.relative_to(config.root)).replace("\\", "/")
    return report


def _review_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "mathematical_verdict": report["mathematical_verdict"],
        "editorial_repair": report["editorial_repair"],
        "summary": report["summary"],
        "repair_notes": report["repair_notes"],
        "confidence": report["confidence"],
        "report_sha256": report["report_sha256"],
        "report_path": report["report_path"],
        "generated_at": report["generated_at"],
    }


def evaluate_gate(config: AuditConfig, item_state: dict[str, Any]) -> str:
    reviews = item_state.get("reviews", {})
    required = [reviewer.id for reviewer in config.required_reviewers]
    required_reports = [reviews.get(reviewer_id) for reviewer_id in required]

    for report in required_reports:
        if not report:
            continue
        verdict = report.get("mathematical_verdict")
        if verdict == "fail" and config.gate.block_on_fail:
            return "needs_repair"
        if verdict == "uncertain" and config.gate.block_on_uncertain:
            return "needs_repair"

    pass_count = sum(
        1
        for report in required_reports
        if report and report.get("mathematical_verdict") == "pass"
    )
    if config.gate.policy == "all":
        return "closed" if pass_count == len(required) else "in_progress"
    return (
        "closed"
        if pass_count >= config.gate.min_passes
        else "in_progress"
    )


def _advance_after_close(state: dict[str, Any], item_state: dict[str, Any]) -> None:
    item_state["status"] = "closed"
    item_state["closed_at"] = utc_now()
    seen = False
    for candidate in state["items"]:
        if candidate["id"] == item_state["id"]:
            seen = True
            continue
        if seen and candidate["status"] == "pending":
            candidate["status"] = "in_progress"
            break


def _coordinate(config: AuditConfig, state: dict[str, Any], item_state: dict[str, Any]) -> str:
    warnings: list[str] = []
    for reviewer_id, review in item_state.get("reviews", {}).items():
        if review.get("editorial_repair") and review.get("repair_notes"):
            warnings.append(f"{reviewer_id}: {review['repair_notes']}")
    item_state["editorial_warnings"] = warnings
    verdict = evaluate_gate(config, item_state)
    if verdict == "closed":
        _advance_after_close(state, item_state)
    elif verdict == "needs_repair":
        item_state["status"] = "needs_repair"
    else:
        item_state["status"] = "in_progress"
    return verdict


def _make_requests(
    config: AuditConfig,
    state: dict[str, Any],
    item: ItemSpec,
    item_text: str,
    missing: list[ReviewerSpec],
) -> list[ReviewRequest]:
    digest = _state_item(state, item.id)["content_sha256"]
    return [
        ReviewRequest(
            config=config,
            item=item,
            reviewer=reviewer,
            prompt=build_prompt(config, state, item, reviewer, item_text, digest),
            item_text=item_text,
            generation=state["source"]["generation"],
        )
        for reviewer in missing
    ]


def run_current(project: str | Path) -> dict[str, Any]:
    config = load_config(project)
    with project_lock(config.root):
        state = load_state(config.root)
        ensure_config_matches_state(config, state)
        active = current_item(state)
        if active is None:
            return {"action": "complete", "message": "all audit items are terminal"}
        if active["status"] == "needs_repair":
            return {
                "action": "needs_repair",
                "item_id": active["id"],
                "message": "edit the source, then run proofaudit refresh",
            }
        actual_source_hash = sha256_file(config.source_path)
        if actual_source_hash != state["source"]["sha256"]:
            raise ConfigError(
                "authoritative source changed; run 'proofaudit refresh PROJECT' first"
            )

        item = ensure_item(config, active["id"])
        document = load_document(config.source_path, config.source_kind)
        digest = item_hash(document, item)
        if digest != active["content_sha256"]:
            raise ConfigError(
                f"item {item.id} content changed; run 'proofaudit refresh PROJECT'"
            )
        item_text = document.extract(item)
        missing = [
            reviewer
            for reviewer in config.reviewers
            if reviewer.id not in active.get("reviews", {})
        ]
        if not missing:
            verdict = _coordinate(config, state, active)
            save_state(config.root, state)
            return {"action": verdict, "item_id": item.id, "new_reports": []}

        runner = make_runner(config)
        requests = _make_requests(config, state, item, item_text, missing)
        outputs: dict[str, dict[str, Any] | None] = {}
        errors: dict[str, str] = {}

        if config.runner.parallel and len(requests) > 1 and config.runner.kind != "manual":
            with ThreadPoolExecutor(max_workers=len(requests)) as executor:
                futures = {
                    executor.submit(runner.run, request): request for request in requests
                }
                for future in as_completed(futures):
                    request = futures[future]
                    try:
                        outputs[request.reviewer.id] = future.result()
                    except Exception as exc:
                        errors[request.reviewer.id] = str(exc)
        else:
            for request in requests:
                try:
                    outputs[request.reviewer.id] = runner.run(request)
                except Exception as exc:
                    errors[request.reviewer.id] = str(exc)

        new_reports: list[str] = []
        for reviewer in missing:
            payload = outputs.get(reviewer.id)
            if payload is None:
                continue
            report = _save_report(config, state, item, reviewer, payload)
            active.setdefault("reviews", {})[reviewer.id] = _review_summary(report)
            new_reports.append(reviewer.id)
            append_event(
                config.root,
                "review_recorded",
                item_id=item.id,
                reviewer_id=reviewer.id,
                verdict=report["mathematical_verdict"],
                report_sha256=report["report_sha256"],
            )

        if config.runner.kind == "manual":
            active["status"] = "in_progress"
            save_state(config.root, state)
            append_event(
                config.root,
                "manual_prompts_created",
                item_id=item.id,
                reviewers=[reviewer.id for reviewer in missing],
            )
            return {
                "action": "awaiting_manual_reports",
                "item_id": item.id,
                "reviewers": [reviewer.id for reviewer in missing],
                "errors": errors,
            }

        verdict = _coordinate(config, state, active)
        save_state(config.root, state)
        append_event(
            config.root,
            "item_coordinated",
            item_id=item.id,
            status=active["status"],
            new_reports=new_reports,
            errors=errors,
        )
        return {
            "action": verdict,
            "item_id": item.id,
            "new_reports": new_reports,
            "errors": errors,
        }


def run_until_blocked(project: str | Path) -> list[dict[str, Any]]:
    outcomes: list[dict[str, Any]] = []
    while True:
        outcome = run_current(project)
        outcomes.append(outcome)
        if outcome["action"] != "closed":
            return outcomes


def import_report(
    project: str | Path, item_id: str, reviewer_id: str, report_file: str | Path
) -> dict[str, Any]:
    config = load_config(project)
    with project_lock(config.root):
        state = load_state(config.root)
        ensure_config_matches_state(config, state)
        active = current_item(state)
        if active is None or active["id"] != item_id:
            raise ConfigError(f"manual reports may be submitted only for current item {active and active['id']}")
        reviewer = next(
            (candidate for candidate in config.reviewers if candidate.id == reviewer_id),
            None,
        )
        if reviewer is None:
            raise ConfigError(f"unknown reviewer: {reviewer_id}")
        item = ensure_item(config, item_id)
        payload = parse_review_text(Path(report_file).read_text(encoding="utf-8"))
        report = _save_report(config, state, item, reviewer, payload)
        active.setdefault("reviews", {})[reviewer.id] = _review_summary(report)
        verdict = _coordinate(config, state, active)
        save_state(config.root, state)
        append_event(
            config.root,
            "manual_review_imported",
            item_id=item_id,
            reviewer_id=reviewer_id,
            verdict=report["mathematical_verdict"],
            item_status=active["status"],
        )
        return {
            "action": verdict,
            "item_id": item_id,
            "reviewer_id": reviewer_id,
            "report_sha256": report["report_sha256"],
        }


def skip_current(project: str | Path, reason: str) -> dict[str, Any]:
    if not reason.strip():
        raise ConfigError("skip reason must be non-empty")
    config = load_config(project)
    with project_lock(config.root):
        state = load_state(config.root)
        active = current_item(state)
        if active is None:
            raise ConfigError("no current item to skip")
        active["status"] = "skipped"
        active["skip_reason"] = reason.strip()
        active["closed_at"] = None
        for candidate in state["items"]:
            if candidate["status"] == "pending":
                candidate["status"] = "in_progress"
                break
        save_state(config.root, state)
        append_event(
            config.root, "item_skipped", item_id=active["id"], reason=reason.strip()
        )
        return {"action": "skipped", "item_id": active["id"]}

