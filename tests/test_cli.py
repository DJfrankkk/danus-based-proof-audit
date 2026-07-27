from __future__ import annotations

from proofaudit.cli import main
from proofaudit.state import load_state


def test_init_accepts_any_reviewer_count(tmp_path):
    source = tmp_path / "source.md"
    source.write_text(
        "# Claim\nA short claim.\n\n## Proof\nA short proof.\n",
        encoding="utf-8",
    )
    project = tmp_path / "audit"
    arguments = [
        "init",
        str(source),
        "--project",
        str(project),
        "--runner",
        "mock",
    ]
    for index in range(1, 6):
        arguments.extend(
            ["--reviewer", f"lens-{index}=Independent review lens {index}"]
        )

    assert main(arguments) == 0
    state = load_state(project)
    assert len(state["reviewers"]) == 5
    assert state["gate"]["policy"] == "all"

