# Proof Audit Pipeline

Proof Audit Pipeline is a local, sequential review workflow for mathematical
proofs. It is intended for fast, structured checking when a Lean formalization
is unavailable or disproportionate.

It does not turn an AI verdict into a formal proof certificate. Its purpose is
to make informal review more disciplined, reproducible, and easy to inspect.

## What it records

- An ordered list of claims, lemmas, or page ranges.
- Any number of independent reviewers, each with a custom review lens.
- An `all` gate or a configurable `quorum` gate.
- Exact SHA-256 hashes for the source, each audit item, and every report.
- Separate mathematical verdicts and non-blocking editorial repairs.
- A closed-prefix ledger, so later reviews know exactly what earlier work passed.
- Automatic invalidation from the first affected item after a source, panel, gate,
  or item-order change.
- A read-only local dashboard plus Markdown and HTML reports.

The default runner is `manual`: it prepares prompts but makes no model calls.
The `codex` runner is opt-in and may consume your configured model quota.

## Install

```console
python -m pip install -e .
```

For PDF input and development tools:

```console
python -m pip install -e ".[pdf,dev]"
```

Python 3.11 or newer is required.

## Quick start

Create an audit from a Markdown, TeX, text, or PDF proof:

```console
proofaudit init proof.pdf --project audits/my-proof --mode strict
```

Choose any reviewer count by repeating `--reviewer`:

```console
proofaudit init proof.tex --project audits/my-proof \
  --reviewer "logic=Check statements, typing, quantifiers, and deductions." \
  --reviewer "foundations=Track ambient models, absoluteness, and uses of Choice." \
  --reviewer "adversarial=Try to construct counterexamples and expose hidden assumptions." \
  --gate all
```

Generate prompts for the current item:

```console
proofaudit run audits/my-proof
```

Manual prompts appear under `.proofaudit/outbox/`. Submit a JSON response using
the bundled schema:

```console
proofaudit schema
proofaudit submit audits/my-proof 001 logic review.json
```

Inspect progress or start the dashboard:

```console
proofaudit status audits/my-proof
proofaudit serve audits/my-proof --port 8765
```

Then open `http://127.0.0.1:8765/`.

## Automated runners

Edit `[runner]` in `audit.toml`:

```toml
[runner]
kind = "codex"
executable = "codex"
model = ""
reasoning_effort = "high"
timeout_seconds = 1800
parallel = true
```

`model = ""` uses the CLI default. Reviewers for one item can run in parallel,
but items always close in configured order. For deterministic demos and CI, use
`kind = "mock"`; it never calls a model.

Run one item or continue until a repair is needed:

```console
proofaudit run audits/my-proof
proofaudit run audits/my-proof --until-blocked
```

## Gates

There is no fixed reviewer count. Add or remove `[[reviewers]]` blocks in
`audit.toml`.

With `policy = "all"`, every required reviewer must pass. With
`policy = "quorum"`, at least `min_passes` required reviewers must pass. A
required `fail` or `uncertain` verdict blocks progress by default, even after the
numerical quorum is reached.

Reviewers marked `required = false` contribute evidence but do not control the
gate.

## Repair cycle

1. A `critical` finding produces `fail`; an unresolved `gap` produces
   `uncertain`.
2. The item becomes `needs_repair`, and later items remain pending.
3. Edit the authoritative source.
4. Run `proofaudit refresh PROJECT`.
5. The ledger keeps the unaffected closed prefix and reopens the first changed
   item and everything after it.

Changing the reviewer panel, reviewer instructions, gate, or item order reopens
the affected audit because the earlier evidence no longer matches the protocol.

## Reports

```console
proofaudit report audits/my-proof --format both
```

This writes `.proofaudit/AUDIT_REPORT.md` and
`.proofaudit/AUDIT_REPORT.html`. Reports preserve individual verdicts, findings,
source generations, and hashes. They prominently state that the evidence is AI
review rather than formal verification.

## Included examples

- `examples/minimal` is a complete no-network demo using the deterministic mock
  runner.
- `examples/amorphous-dual-dedekind` is a four-reviewer audit plan for Yifan Hu,
  Ruihuan Mao, and Guozhen Shen, *Amorphous sets and dual Dedekind finiteness*,
  APAL 177 (2026), 103723.

The publisher PDF is not redistributed. The example README explains where to
place a legally obtained local copy.

## Project files

```text
audit.toml                  review panel, gate, runner, and ordered items
.proofaudit/state.json      machine-readable current state
.proofaudit/ledger.md       human-readable sequential ledger
.proofaudit/reports/        immutable report evidence by source generation
.proofaudit/events.jsonl    append-only workflow events
```

Runner transcripts and generated prompts are ignored by Git because they may
contain unpublished proof text. Review the repository history before publishing
an audit of confidential material.

## Development

```console
python -m compileall -q src tests
pytest
proofaudit run examples/minimal --until-blocked
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidance.

