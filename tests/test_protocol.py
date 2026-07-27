from __future__ import annotations

import pytest

from proofaudit.protocol import ReviewValidationError, validate_review


def passing_review(**overrides):
    payload = {
        "mathematical_verdict": "pass",
        "editorial_repair": False,
        "summary": "The argument passed this review lens.",
        "findings": [],
        "repair_notes": "",
        "assumptions_checked": ["Definitions"],
        "counterexamples_tried": ["Empty boundary case"],
        "confidence": 0.8,
    }
    payload.update(overrides)
    return payload


def test_pass_can_preserve_editorial_repair():
    payload = passing_review(
        editorial_repair=True,
        findings=[
            {
                "severity": "warning",
                "location": "line 4",
                "issue": "The antecedent is difficult to parse.",
                "suggested_fix": "Split the sentence.",
            }
        ],
        repair_notes="Split the sentence.",
    )
    assert validate_review(payload) is payload


@pytest.mark.parametrize(
    ("verdict", "severity"),
    [
        ("pass", "gap"),
        ("pass", "critical"),
        ("fail", "warning"),
        ("uncertain", "critical"),
    ],
)
def test_inconsistent_verdicts_are_rejected(verdict, severity):
    payload = passing_review(
        mathematical_verdict=verdict,
        findings=[
            {
                "severity": severity,
                "location": "line 2",
                "issue": "Fixture inconsistency.",
                "suggested_fix": "Repair it.",
            }
        ],
    )
    with pytest.raises(ReviewValidationError):
        validate_review(payload)


def test_editorial_notes_require_editorial_flag():
    with pytest.raises(ReviewValidationError):
        validate_review(passing_review(repair_notes="Clarify this."))

