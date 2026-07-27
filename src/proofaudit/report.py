from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from .config import load_config
from .state import load_state


def _read_full_report(root: Path, review: dict[str, Any]) -> dict[str, Any] | None:
    relative = review.get("report_path")
    if not relative:
        return None
    path = (root / relative).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return None
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def markdown_report(root: Path, state: dict[str, Any]) -> str:
    reviewers = state.get("reviewers", [])
    source = state["source"]
    terminal = sum(
        item["status"] in {"closed", "skipped"} for item in state["items"]
    )
    lines = [
        f"# Proof audit report: {state['project_name']}",
        "",
        "> Evidence level: independent AI review. This report is not a formal proof certificate.",
        "",
        f"- Source: `{source['path']}`",
        f"- Source generation: `{source['generation']}`",
        f"- Source SHA-256: `{source['sha256']}`",
        f"- Progress: `{terminal}/{len(state['items'])}` terminal items",
        f"- Reviewer panel: `{len(reviewers)}` configured reviewers",
        "",
        "## Sequential ledger",
        "",
    ]
    header = ["Seq.", "Item", "Location", "Status", *[r["id"] for r in reviewers]]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join(["---:" if i == 0 else "---" for i in range(len(header))]) + "|")
    for index, item in enumerate(state["items"], 1):
        row = [
            f"{index:03d}",
            item["title"].replace("|", "\\|"),
            f"{item['unit']} {item['start']}-{item['end']}",
            f"`{item['status']}`",
        ]
        for reviewer in reviewers:
            review = item.get("reviews", {}).get(reviewer["id"])
            row.append(
                f"`{review['mathematical_verdict']}`"
                if review
                else ""
            )
        lines.append("| " + " | ".join(row) + " |")

    lines.extend(["", "## Item evidence", ""])
    for item in state["items"]:
        lines.extend(
            [
                f"### {item['id']} — {item['title']}",
                "",
                f"- Status: `{item['status']}`",
                f"- Location: `{item['unit']} {item['start']}-{item['end']}`",
                f"- Item SHA-256: `{item['content_sha256']}`",
            ]
        )
        if item.get("skip_reason"):
            lines.append(f"- Skip reason: {item['skip_reason']}")
        for reviewer in reviewers:
            summary = item.get("reviews", {}).get(reviewer["id"])
            if not summary:
                continue
            lines.extend(
                [
                    "",
                    f"**{reviewer['name']} (`{reviewer['id']}`): "
                    f"{summary['mathematical_verdict']}**",
                    "",
                    summary.get("summary", ""),
                ]
            )
            report = _read_full_report(root, summary)
            if report:
                for finding in report.get("findings", []):
                    lines.append(
                        f"- `{finding['severity']}` at {finding['location']}: "
                        f"{finding['issue']}"
                    )
                if report.get("editorial_repair"):
                    lines.append(f"- Editorial repair: {report['repair_notes']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def html_report(root: Path, state: dict[str, Any]) -> str:
    reviewers = state.get("reviewers", [])
    source = state["source"]
    rows: list[str] = []
    details: list[str] = []
    for index, item in enumerate(state["items"], 1):
        cells = [
            f"<td>{index:03d}</td>",
            f"<td><strong>{html.escape(item['title'])}</strong></td>",
            f"<td>{html.escape(item['unit'])} {item['start']}-{item['end']}</td>",
            f"<td><span class='status {html.escape(item['status'])}'>{html.escape(item['status'])}</span></td>",
        ]
        for reviewer in reviewers:
            review = item.get("reviews", {}).get(reviewer["id"])
            verdict = review.get("mathematical_verdict", "") if review else ""
            cells.append(f"<td>{html.escape(verdict)}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")

        evidence = []
        for reviewer in reviewers:
            summary = item.get("reviews", {}).get(reviewer["id"])
            if not summary:
                continue
            report = _read_full_report(root, summary)
            findings = ""
            if report:
                findings = "".join(
                    "<li><code>"
                    + html.escape(finding["severity"])
                    + "</code> "
                    + html.escape(finding["location"])
                    + ": "
                    + html.escape(finding["issue"])
                    + "</li>"
                    for finding in report.get("findings", [])
                )
            evidence.append(
                "<section class='review'><h4>"
                + html.escape(reviewer["name"])
                + " — "
                + html.escape(summary["mathematical_verdict"])
                + "</h4><p>"
                + html.escape(summary.get("summary", ""))
                + "</p>"
                + (f"<ul>{findings}</ul>" if findings else "")
                + "</section>"
            )
        details.append(
            "<article><h3>"
            + html.escape(f"{item['id']} — {item['title']}")
            + "</h3><p class='meta'>"
            + html.escape(f"{item['unit']} {item['start']}-{item['end']}")
            + " · "
            + html.escape(item["content_sha256"])
            + "</p>"
            + "".join(evidence)
            + "</article>"
        )

    reviewer_headers = "".join(
        f"<th>{html.escape(reviewer['id'])}</th>" for reviewer in reviewers
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(state['project_name'])} — proof audit</title>
<style>
:root {{ color-scheme: light; --ink:#1d252c; --muted:#66727c; --line:#d9dee2;
--green:#16784b; --amber:#9a6700; --red:#b42318; --blue:#2457a6; --paper:#fff; --wash:#f5f7f8; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; color:var(--ink); background:var(--paper); font:14px/1.5 system-ui,sans-serif; letter-spacing:0; }}
header {{ padding:28px max(24px,calc((100vw - 1180px)/2)); border-bottom:1px solid var(--line); background:var(--wash); }}
main {{ max-width:1180px; margin:0 auto; padding:24px; }}
h1 {{ margin:0 0 8px; font-size:27px; }}
h2 {{ margin-top:34px; font-size:19px; }}
h3 {{ font-size:16px; }}
.meta {{ color:var(--muted); overflow-wrap:anywhere; }}
.notice {{ border-left:4px solid var(--amber); padding:8px 12px; background:#fff9e8; }}
.table-wrap {{ overflow:auto; border:1px solid var(--line); border-radius:6px; }}
table {{ width:100%; border-collapse:collapse; white-space:nowrap; }}
th,td {{ padding:9px 11px; text-align:left; border-bottom:1px solid var(--line); }}
th {{ background:var(--wash); font-size:12px; text-transform:uppercase; }}
.status {{ font-weight:700; }}
.closed {{ color:var(--green); }} .needs_repair {{ color:var(--red); }}
.in_progress {{ color:var(--blue); }} .skipped {{ color:var(--amber); }}
article {{ padding:18px 0; border-bottom:1px solid var(--line); }}
.review {{ padding:8px 0; }} .review h4 {{ margin:0 0 5px; }}
code {{ background:var(--wash); padding:1px 4px; border-radius:3px; }}
</style>
</head>
<body>
<header>
  <h1>{html.escape(state['project_name'])}</h1>
  <div class="meta">Generation {source['generation']} · SHA-256 {html.escape(source['sha256'])}</div>
</header>
<main>
  <p class="notice">Independent AI review; not formal verification.</p>
  <h2>Sequential ledger</h2>
  <div class="table-wrap"><table>
    <thead><tr><th>Seq.</th><th>Item</th><th>Location</th><th>Status</th>{reviewer_headers}</tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table></div>
  <h2>Evidence</h2>
  {''.join(details)}
</main>
</body>
</html>
"""


def write_reports(project: str | Path, formats: tuple[str, ...] = ("markdown", "html")) -> list[Path]:
    config = load_config(project)
    state = load_state(config.root)
    output_dir = config.root / ".proofaudit"
    paths: list[Path] = []
    if "markdown" in formats:
        path = output_dir / "AUDIT_REPORT.md"
        path.write_text(markdown_report(config.root, state), encoding="utf-8")
        paths.append(path)
    if "html" in formats:
        path = output_dir / "AUDIT_REPORT.html"
        path.write_text(html_report(config.root, state), encoding="utf-8")
        paths.append(path)
    return paths

