from __future__ import annotations

from pathlib import Path

import pytest

from proofaudit.config import write_config
from proofaudit.models import (
    AuditConfig,
    GateConfig,
    ItemSpec,
    ReviewerSpec,
    RunnerConfig,
)
from proofaudit.source import load_document
from proofaudit.state import bootstrap


@pytest.fixture
def make_project():
    def factory(
        root: Path,
        text: str,
        *,
        reviewer_count: int = 3,
        gate: GateConfig | None = None,
        runner: str = "mock",
    ) -> AuditConfig:
        root.mkdir(parents=True)
        source = root / "proof.md"
        source.write_text(text, encoding="utf-8")
        reviewers = tuple(
            ReviewerSpec(
                f"reviewer-{index}",
                f"Reviewer {index}",
                f"Independent lens {index}",
            )
            for index in range(1, reviewer_count + 1)
        )
        config = AuditConfig(
            root=root,
            name="Test proof",
            source="proof.md",
            source_kind="markdown",
            mode="custom",
            runner=RunnerConfig(kind=runner, timeout_seconds=30),
            gate=gate or GateConfig(policy="all"),
            reviewers=reviewers,
            items=(
                ItemSpec("001", "First claim", "lines", 1, 2),
                ItemSpec("002", "Second claim", "lines", 3, 4),
                ItemSpec("003", "Third claim", "lines", 5, 6),
            ),
        )
        write_config(config)
        bootstrap(config, load_document(source, "markdown"))
        return config

    return factory

