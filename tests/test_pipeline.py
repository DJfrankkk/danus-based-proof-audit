from __future__ import annotations

from dataclasses import replace

import pytest

from proofaudit.config import write_config
from proofaudit.coordinator import evaluate_gate, run_current, run_until_blocked
from proofaudit.models import ConfigError, GateConfig, ReviewerSpec
from proofaudit.report import write_reports
from proofaudit.source import load_document
from proofaudit.state import (
    ensure_config_matches_state,
    load_state,
    refresh_state,
)


BASE_PROOF = """# First
First argument.
# Second
Second argument.
# Third
Third argument.
"""


def test_arbitrary_panel_closes_sequentially_and_preserves_warning(
    tmp_path, make_project
):
    config = make_project(
        tmp_path / "audit",
        BASE_PROOF.replace("Second argument.", "Second argument. AUDIT_WARN"),
        reviewer_count=4,
    )

    outcomes = run_until_blocked(config.root)
    state = load_state(config.root)

    assert outcomes[-1]["action"] == "complete"
    assert [item["status"] for item in state["items"]] == [
        "closed",
        "closed",
        "closed",
    ]
    assert all(len(item["reviews"]) == 4 for item in state["items"])
    assert len(state["items"][1]["editorial_warnings"]) == 4

    paths = write_reports(config.root)
    assert {path.suffix for path in paths} == {".md", ".html"}
    assert "not a formal proof certificate" in paths[0].read_text(encoding="utf-8")


def test_failure_blocks_the_prefix_and_does_not_advance(tmp_path, make_project):
    config = make_project(
        tmp_path / "audit",
        BASE_PROOF.replace("First argument.", "First argument. AUDIT_FAIL"),
        reviewer_count=2,
    )

    outcome = run_current(config.root)
    state = load_state(config.root)

    assert outcome["action"] == "needs_repair"
    assert state["items"][0]["status"] == "needs_repair"
    assert state["items"][1]["status"] == "pending"
    assert state["items"][2]["status"] == "pending"


def test_quorum_needs_enough_passes_and_honors_blocking_dissent(
    tmp_path, make_project
):
    gate = GateConfig(policy="quorum", min_passes=2)
    config = make_project(
        tmp_path / "audit", BASE_PROOF, reviewer_count=3, gate=gate
    )
    item = {
        "reviews": {
            "reviewer-1": {"mathematical_verdict": "pass"},
            "reviewer-2": {"mathematical_verdict": "pass"},
        }
    }
    assert evaluate_gate(config, item) == "closed"

    item["reviews"]["reviewer-3"] = {"mathematical_verdict": "uncertain"}
    assert evaluate_gate(config, item) == "needs_repair"


def test_source_edit_reopens_only_changed_item_and_suffix(tmp_path, make_project):
    config = make_project(tmp_path / "audit", BASE_PROOF, reviewer_count=3)
    run_until_blocked(config.root)

    changed = BASE_PROOF.replace("Second argument.", "Repaired second argument.")
    config.source_path.write_text(changed, encoding="utf-8")
    state, first_changed = refresh_state(
        config,
        load_state(config.root),
        load_document(config.source_path, "markdown"),
    )

    assert first_changed == 1
    assert state["source"]["generation"] == 2
    assert [item["status"] for item in state["items"]] == [
        "closed",
        "in_progress",
        "pending",
    ]
    assert state["items"][0]["reviews"]
    assert state["items"][1]["reviews"] == {}


def test_reviewer_lens_change_requires_refresh_and_reopens_from_start(
    tmp_path, make_project
):
    config = make_project(tmp_path / "audit", BASE_PROOF, reviewer_count=2)
    run_until_blocked(config.root)
    changed_reviewers = (
        ReviewerSpec("reviewer-1", "Reviewer 1", "A materially new lens"),
        config.reviewers[1],
    )
    changed_config = replace(config, reviewers=changed_reviewers)
    write_config(changed_config)

    with pytest.raises(ConfigError):
        ensure_config_matches_state(changed_config, load_state(config.root))

    state, first_changed = refresh_state(
        changed_config,
        load_state(config.root),
        load_document(config.source_path, "markdown"),
    )
    assert first_changed == 0
    assert [item["status"] for item in state["items"]] == [
        "in_progress",
        "pending",
        "pending",
    ]


def test_item_reordering_invalidates_the_new_prefix(tmp_path, make_project):
    config = make_project(tmp_path / "audit", BASE_PROOF, reviewer_count=2)
    run_until_blocked(config.root)
    reordered = replace(
        config,
        items=(config.items[1], config.items[0], config.items[2]),
    )
    write_config(reordered)

    state, first_changed = refresh_state(
        reordered,
        load_state(config.root),
        load_document(config.source_path, "markdown"),
    )
    assert first_changed == 0
    assert state["items"][0]["id"] == "002"
    assert state["items"][0]["status"] == "in_progress"
    assert state["items"][1]["status"] == "pending"
