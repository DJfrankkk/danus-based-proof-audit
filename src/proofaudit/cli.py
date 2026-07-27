from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import __version__
from .config import (
    REVIEWER_PRESETS,
    load_config,
    parse_custom_reviewers,
    write_config,
)
from .coordinator import import_report, run_current, run_until_blocked, skip_current
from .models import AuditConfig, ConfigError, GateConfig, RunnerConfig
from .protocol import ReviewValidationError, schema_path
from .report import write_reports
from .runner import RunnerError
from .server import serve
from .source import detect_kind, load_document, suggest_items
from .state import (
    bootstrap,
    current_item,
    load_state,
    project_lock,
    refresh_state,
)


def _json_print(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _build_init_config(args, root: Path, source_relative: str, source_kind: str, items):
    reviewers = (
        parse_custom_reviewers(args.reviewer)
        if args.reviewer
        else REVIEWER_PRESETS[args.mode]
    )
    min_passes = args.min_passes
    if args.gate == "quorum" and not min_passes:
        min_passes = len([reviewer for reviewer in reviewers if reviewer.required]) // 2 + 1
    return AuditConfig(
        root=root,
        name=args.name or Path(args.source).stem,
        source=source_relative,
        source_kind=source_kind,
        mode="custom" if args.reviewer else args.mode,
        runner=RunnerConfig(
            kind=args.runner,
            executable=args.executable,
            model=args.model,
            reasoning_effort=args.effort,
            timeout_seconds=args.timeout,
            parallel=not args.serial,
        ),
        gate=GateConfig(
            policy=args.gate,
            min_passes=min_passes,
            block_on_fail=True,
            block_on_uncertain=True,
        ),
        reviewers=reviewers,
        items=items,
    )


def command_init(args) -> int:
    source = Path(args.source).resolve()
    if not source.is_file():
        raise ConfigError(f"source not found: {source}")
    root = Path(args.project).resolve()
    if root.exists() and any(root.iterdir()) and not args.force:
        raise ConfigError(f"project directory is not empty: {root}")
    root.mkdir(parents=True, exist_ok=True)
    source_dir = root / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    destination = source_dir / f"authoritative{source.suffix.lower()}"
    shutil.copy2(source, destination)
    kind = detect_kind(destination)
    document = load_document(destination, kind)
    items = suggest_items(document)
    relative = str(destination.relative_to(root)).replace("\\", "/")
    config = _build_init_config(args, root, relative, kind, items)
    write_config(config)
    bootstrap(config, document, force=args.force)
    print(f"Created audit project: {root}")
    print(f"Reviewers: {len(config.reviewers)}")
    print(f"Items: {len(config.items)}")
    return 0


def command_bootstrap(args) -> int:
    config = load_config(args.project)
    document = load_document(config.source_path, config.source_kind)
    bootstrap(config, document, force=args.force)
    print(f"Bootstrapped: {config.root}")
    return 0


def command_refresh(args) -> int:
    config = load_config(args.project)
    if args.source:
        incoming = Path(args.source).resolve()
        if not incoming.is_file():
            raise ConfigError(f"source not found: {incoming}")
        if incoming != config.source_path:
            shutil.copy2(incoming, config.source_path)
    document = load_document(config.source_path, config.source_kind)
    with project_lock(config.root):
        state = load_state(config.root)
        state, first_changed = refresh_state(config, state, document)
    print(
        "Refreshed; first invalidated item: "
        + (state["items"][first_changed]["id"] if first_changed is not None else "none")
    )
    return 0


def command_run(args) -> int:
    outcomes = (
        run_until_blocked(args.project)
        if args.until_blocked
        else [run_current(args.project)]
    )
    _json_print(outcomes if args.until_blocked else outcomes[0])
    return 1 if outcomes[-1]["action"] == "needs_repair" else 0


def command_submit(args) -> int:
    _json_print(import_report(args.project, args.item, args.reviewer, args.report))
    return 0


def command_skip(args) -> int:
    _json_print(skip_current(args.project, args.reason))
    return 0


def command_status(args) -> int:
    config = load_config(args.project)
    state = load_state(config.root)
    if args.json:
        _json_print(state)
        return 0
    active = current_item(state)
    terminal = sum(
        item["status"] in {"closed", "skipped"} for item in state["items"]
    )
    print(f"{state['project_name']}  generation {state['source']['generation']}")
    print(f"progress {terminal}/{len(state['items'])}")
    print(f"current  {active['id'] + ' ' + active['title'] if active else 'complete'}")
    print()
    reviewer_ids = [reviewer["id"] for reviewer in state["reviewers"]]
    print("SEQ  STATUS          ITEM")
    for index, item in enumerate(state["items"], 1):
        verdicts = ", ".join(
            f"{reviewer_id}:{item.get('reviews', {}).get(reviewer_id, {}).get('mathematical_verdict', '-')}"
            for reviewer_id in reviewer_ids
        )
        print(f"{index:03d}  {item['status']:<14}  {item['title']}  [{verdicts}]")
    return 0


def command_reviewers(args) -> int:
    config = load_config(args.project)
    print(f"gate: {config.gate.policy}", end="")
    if config.gate.policy == "quorum":
        print(f" ({config.gate.min_passes} passes)")
    else:
        print()
    for reviewer in config.reviewers:
        marker = "required" if reviewer.required else "optional"
        print(f"- {reviewer.id} ({marker}): {reviewer.lens}")
    return 0


def command_report(args) -> int:
    formats = ("markdown", "html") if args.format == "both" else (args.format,)
    for path in write_reports(args.project, formats):
        print(path)
    return 0


def command_serve(args) -> int:
    serve(args.project, args.host, args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="proofaudit",
        description="Configurable, evidence-preserving AI review for mathematical proofs.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="create an audit project from a proof source")
    init.add_argument("source")
    init.add_argument("--project", required=True)
    init.add_argument("--name")
    init.add_argument("--mode", choices=sorted(REVIEWER_PRESETS), default="quick")
    init.add_argument(
        "--reviewer",
        action="append",
        default=[],
        metavar="ID=LENS",
        help="custom reviewer; repeat to choose any panel size",
    )
    init.add_argument("--gate", choices=["all", "quorum"], default="all")
    init.add_argument("--min-passes", type=int, default=0)
    init.add_argument("--runner", choices=["manual", "mock", "codex"], default="manual")
    init.add_argument("--executable", default="codex")
    init.add_argument("--model", default="")
    init.add_argument("--effort", default="high")
    init.add_argument("--timeout", type=int, default=1800)
    init.add_argument("--serial", action="store_true")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=command_init)

    boot = sub.add_parser("bootstrap", help="create state from an existing audit.toml")
    boot.add_argument("project")
    boot.add_argument("--force", action="store_true")
    boot.set_defaults(func=command_bootstrap)

    refresh = sub.add_parser("refresh", help="reconcile source or config changes")
    refresh.add_argument("project")
    refresh.add_argument("--source")
    refresh.set_defaults(func=command_refresh)

    run = sub.add_parser("run", help="review the current sequential item")
    run.add_argument("project")
    run.add_argument("--until-blocked", action="store_true")
    run.set_defaults(func=command_run)

    submit = sub.add_parser("submit", help="import a manual reviewer JSON report")
    submit.add_argument("project")
    submit.add_argument("item")
    submit.add_argument("reviewer")
    submit.add_argument("report")
    submit.set_defaults(func=command_submit)

    skip = sub.add_parser("skip", help="skip the current item without certifying it")
    skip.add_argument("project")
    skip.add_argument("--reason", required=True)
    skip.set_defaults(func=command_skip)

    status = sub.add_parser("status", help="show the sequential ledger")
    status.add_argument("project")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=command_status)

    reviewers = sub.add_parser("reviewers", help="show configured reviewer panel")
    reviewers.add_argument("project")
    reviewers.set_defaults(func=command_reviewers)

    report = sub.add_parser("report", help="generate reusable audit reports")
    report.add_argument("project")
    report.add_argument(
        "--format", choices=["markdown", "html", "both"], default="both"
    )
    report.set_defaults(func=command_report)

    dashboard = sub.add_parser("serve", help="serve the read-only local dashboard")
    dashboard.add_argument("project")
    dashboard.add_argument("--host", default="127.0.0.1")
    dashboard.add_argument("--port", type=int, default=8765)
    dashboard.set_defaults(func=command_serve)

    schema = sub.add_parser("schema", help="print the reviewer JSON schema path")
    schema.set_defaults(func=lambda _args: (print(schema_path()) or 0))
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (ConfigError, ReviewValidationError, RunnerError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130

