from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .models import AuditConfig, ItemSpec, ReviewerSpec
from .protocol import parse_review_text, schema_path


class RunnerError(RuntimeError):
    """Raised when a reviewer process cannot produce a valid report."""


@dataclass
class ReviewRequest:
    config: AuditConfig
    item: ItemSpec
    reviewer: ReviewerSpec
    prompt: str
    item_text: str
    generation: int


class BaseRunner:
    def run(self, request: ReviewRequest) -> dict | None:
        raise NotImplementedError


class ManualRunner(BaseRunner):
    def run(self, request: ReviewRequest) -> None:
        outbox = (
            request.config.root
            / ".proofaudit"
            / "outbox"
            / f"g{request.generation:03d}"
            / request.item.id
        )
        outbox.mkdir(parents=True, exist_ok=True)
        path = outbox / f"{request.reviewer.id}.prompt.md"
        path.write_text(request.prompt, encoding="utf-8")
        return None


class MockRunner(BaseRunner):
    """Deterministic runner used by the demo and CI."""

    def run(self, request: ReviewRequest) -> dict:
        text = request.item_text
        if "AUDIT_FAIL" in text:
            return {
                "mathematical_verdict": "fail",
                "editorial_repair": False,
                "summary": "The deterministic fixture contains a critical-failure marker.",
                "findings": [
                    {
                        "severity": "critical",
                        "location": f"{request.item.unit} {request.item.start}-{request.item.end}",
                        "issue": "AUDIT_FAIL marker requests a failing fixture verdict.",
                        "suggested_fix": "Remove the marker after repairing the fixture.",
                    }
                ],
                "repair_notes": "",
                "assumptions_checked": ["Fixture marker semantics"],
                "counterexamples_tried": ["Deterministic marker test"],
                "confidence": 1.0,
            }
        if "AUDIT_GAP" in text:
            return {
                "mathematical_verdict": "uncertain",
                "editorial_repair": False,
                "summary": "The deterministic fixture contains an unresolved-gap marker.",
                "findings": [
                    {
                        "severity": "gap",
                        "location": f"{request.item.unit} {request.item.start}-{request.item.end}",
                        "issue": "AUDIT_GAP marker requests an unresolved fixture verdict.",
                        "suggested_fix": "Supply the missing argument and remove the marker.",
                    }
                ],
                "repair_notes": "",
                "assumptions_checked": ["Fixture marker semantics"],
                "counterexamples_tried": ["Deterministic marker test"],
                "confidence": 1.0,
            }
        warning = "AUDIT_WARN" in text
        return {
            "mathematical_verdict": "pass",
            "editorial_repair": warning,
            "summary": f"Deterministic pass from reviewer {request.reviewer.id}.",
            "findings": (
                [
                    {
                        "severity": "warning",
                        "location": f"{request.item.unit} {request.item.start}-{request.item.end}",
                        "issue": "AUDIT_WARN marker requests an editorial warning.",
                        "suggested_fix": "Clarify the marked passage.",
                    }
                ]
                if warning
                else []
            ),
            "repair_notes": "Clarify the marked passage." if warning else "",
            "assumptions_checked": ["Fixture marker semantics"],
            "counterexamples_tried": ["No failing marker was present"],
            "confidence": 1.0,
        }


def _command_prefix(executable: str) -> list[str]:
    resolved = executable
    if not os.path.isabs(resolved):
        resolved = shutil.which(resolved) or resolved
    suffix = Path(resolved).suffix.lower()
    if os.name == "nt" and suffix in {".cmd", ".bat"}:
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", resolved]
    return [resolved]


class CodexRunner(BaseRunner):
    def run(self, request: ReviewRequest) -> dict:
        runner = request.config.runner
        output_dir = (
            request.config.root
            / ".proofaudit"
            / "logs"
            / f"g{request.generation:03d}"
            / request.item.id
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        result_path = output_dir / f"{request.reviewer.id}.result.json"
        log_path = output_dir / f"{request.reviewer.id}.log"

        command = [
            *_command_prefix(runner.executable),
            "exec",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--ephemeral",
            "--ignore-rules",
            "--color",
            "never",
            "-C",
            str(request.config.root),
            "--output-schema",
            schema_path(),
            "-o",
            str(result_path),
        ]
        if runner.model:
            command.extend(["--model", runner.model])
        if runner.reasoning_effort:
            command.extend(
                [
                    "--config",
                    f'model_reasoning_effort="{runner.reasoning_effort}"',
                ]
            )
        command.append("-")

        try:
            completed = subprocess.run(
                command,
                input=request.prompt,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=request.config.root,
                timeout=runner.timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RunnerError(
                f"Codex executable not found: {runner.executable}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            log_path.write_text(
                (exc.stdout or "") + f"\nTimed out after {runner.timeout_seconds}s\n",
                encoding="utf-8",
            )
            raise RunnerError(
                f"reviewer {request.reviewer.id} timed out; see {log_path}"
            ) from exc

        log_path.write_text(completed.stdout or "", encoding="utf-8")
        if completed.returncode != 0:
            raise RunnerError(
                f"reviewer {request.reviewer.id} exited {completed.returncode}; "
                f"see {log_path}"
            )
        if not result_path.is_file():
            raise RunnerError(
                f"reviewer {request.reviewer.id} produced no result; see {log_path}"
            )
        return parse_review_text(result_path.read_text(encoding="utf-8"))


def make_runner(config: AuditConfig) -> BaseRunner:
    if config.runner.kind == "manual":
        return ManualRunner()
    if config.runner.kind == "mock":
        return MockRunner()
    if config.runner.kind == "codex":
        return CodexRunner()
    raise RunnerError(f"unsupported runner kind: {config.runner.kind}")

