# Minimal no-network demo

This example uses the deterministic `mock` runner. It demonstrates:

- two configurable reviewers;
- strict sequential advancement;
- a passing item that retains a non-blocking editorial warning; and
- complete Markdown/HTML report generation without a model call.

Run:

```console
proofaudit bootstrap examples/minimal
proofaudit run examples/minimal --until-blocked
proofaudit report examples/minimal --format both
proofaudit serve examples/minimal --port 8765
```

Use `proofaudit bootstrap examples/minimal --force` to reset the demo.

