from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

from .models import AuditConfig, ConfigError, ItemSpec, ReviewerSpec


class ReviewValidationError(ValueError):
    """Raised when a reviewer result violates the evidence contract."""


def schema_path() -> str:
    return str(files("proofaudit").joinpath("assets/review.schema.json"))


def validate_review(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ReviewValidationError("review must be a JSON object")

    expected = {
        "mathematical_verdict",
        "editorial_repair",
        "summary",
        "findings",
        "repair_notes",
        "assumptions_checked",
        "counterexamples_tried",
        "confidence",
    }
    missing = expected - payload.keys()
    extra = payload.keys() - expected
    if missing:
        raise ReviewValidationError(f"missing review fields: {sorted(missing)}")
    if extra:
        raise ReviewValidationError(f"unknown review fields: {sorted(extra)}")

    verdict = payload["mathematical_verdict"]
    if verdict not in {"pass", "fail", "uncertain"}:
        raise ReviewValidationError("invalid mathematical_verdict")
    if not isinstance(payload["editorial_repair"], bool):
        raise ReviewValidationError("editorial_repair must be boolean")
    if not isinstance(payload["summary"], str) or not payload["summary"].strip():
        raise ReviewValidationError("summary must be a non-empty string")
    if not isinstance(payload["repair_notes"], str):
        raise ReviewValidationError("repair_notes must be a string")
    for field in ("assumptions_checked", "counterexamples_tried"):
        if not isinstance(payload[field], list) or not all(
            isinstance(value, str) for value in payload[field]
        ):
            raise ReviewValidationError(f"{field} must be a list of strings")
    confidence = payload["confidence"]
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0 <= confidence <= 1
    ):
        raise ReviewValidationError("confidence must be a number between 0 and 1")

    findings = payload["findings"]
    if not isinstance(findings, list):
        raise ReviewValidationError("findings must be a list")
    severities: list[str] = []
    for index, finding in enumerate(findings, 1):
        if not isinstance(finding, dict):
            raise ReviewValidationError(f"finding {index} must be an object")
        if set(finding) != {"severity", "location", "issue", "suggested_fix"}:
            raise ReviewValidationError(f"finding {index} has invalid fields")
        severity = finding["severity"]
        if severity not in {"critical", "gap", "warning"}:
            raise ReviewValidationError(f"finding {index} has invalid severity")
        for field in ("location", "issue", "suggested_fix"):
            if not isinstance(finding[field], str):
                raise ReviewValidationError(f"finding {index}.{field} must be a string")
        if not finding["location"].strip() or not finding["issue"].strip():
            raise ReviewValidationError(f"finding {index} needs location and issue")
        severities.append(severity)

    has_critical = "critical" in severities
    has_gap = "gap" in severities
    if verdict == "pass" and (has_critical or has_gap):
        raise ReviewValidationError("pass requires zero critical and zero gap findings")
    if verdict == "fail" and not has_critical:
        raise ReviewValidationError("fail requires at least one critical finding")
    if verdict == "uncertain" and (has_critical or not has_gap):
        raise ReviewValidationError(
            "uncertain requires at least one gap and no critical finding"
        )

    if payload["editorial_repair"] and not payload["repair_notes"].strip():
        raise ReviewValidationError("editorial_repair requires non-empty repair_notes")
    if not payload["editorial_repair"] and payload["repair_notes"].strip():
        raise ReviewValidationError(
            "repair_notes must be empty when editorial_repair is false"
        )
    return payload


def parse_review_text(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReviewValidationError(f"review is not valid JSON: {exc}") from exc
    return validate_review(payload)


def closed_prefix_summary(state: dict[str, Any], current_item_id: str) -> str:
    lines: list[str] = []
    for item in state.get("items", []):
        if item["id"] == current_item_id:
            break
        if item.get("status") != "closed":
            continue
        summaries = [
            review.get("summary", "")
            for review in item.get("reviews", {}).values()
            if review.get("summary")
        ]
        warning_count = len(item.get("editorial_warnings", []))
        compact = summaries[0] if summaries else "Panel pass."
        lines.append(
            f"- {item['id']} {item['title']}: {compact}"
            + (f" ({warning_count} editorial warning(s) preserved.)" if warning_count else "")
        )
    return "\n".join(lines) if lines else "- No earlier item is closed."


def build_prompt(
    config: AuditConfig,
    state: dict[str, Any],
    item: ItemSpec,
    reviewer: ReviewerSpec,
    item_text: str,
    item_sha256: str,
) -> str:
    locator = f"{item.unit} {item.start}-{item.end}"
    return f"""You are an independent mathematical proof reviewer.

REVIEWER ID: {reviewer.id}
REVIEWER LENS:
{reviewer.lens}

AUTHORITATIVE SOURCE:
- Project: {config.name}
- File: {config.source}
- Source generation: {state['source']['generation']}
- Source SHA-256: {state['source']['sha256']}
- Audit item: {item.id} — {item.title}
- Location: {locator}
- Item SHA-256: {item_sha256}

CLOSED PREFIX (accepted context, including retained caveats):
{closed_prefix_summary(state, item.id)}

AUDIT CONTRACT:
1. Review only this item, while checking every dependency it uses from the closed prefix.
2. Check the exact mathematical statement, not a nearby theorem.
3. Do not edit the source or audit files.
4. Try to falsify the argument before accepting it.
5. Distinguish a mathematical critical error or proof gap from a readability/editorial repair.
6. Use "pass" only when there are zero critical and zero gap findings.
7. Use "fail" only with at least one concrete critical finding.
8. Use "uncertain" only with at least one concrete unresolved gap and no critical finding.
9. A passing review may still set editorial_repair=true and preserve repair notes.
10. Give precise page/line locations and return only JSON matching the supplied schema.

ITEM TEXT:
<<<BEGIN AUDIT ITEM>>>
{item_text}
<<<END AUDIT ITEM>>>
"""


def ensure_item(config: AuditConfig, item_id: str) -> ItemSpec:
    for item in config.items:
        if item.id == item_id:
            return item
    raise ConfigError(f"unknown item id: {item_id}")

