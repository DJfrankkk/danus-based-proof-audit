# Amorphous sets and dual Dedekind finiteness

This directory is a worked configuration for:

Yifan Hu, Ruihuan Mao, and Guozhen Shen, "Amorphous sets and dual Dedekind
finiteness", *Annals of Pure and Applied Logic* 177 (2026), 103723,
DOI: `10.1016/j.apal.2026.103723`.

The paper is used as the primary real-world example because it contains a
compact chain of model-theoretic and permutation-model arguments that benefits
from several independent review lenses.

## Source file

The publisher PDF is copyrighted and is not committed to this repository. Place
a legally obtained copy at:

```text
source/Amorphous sets and dual Dedekind finiteness.pdf
```

Then run:

```console
proofaudit bootstrap examples/amorphous-dual-dedekind
proofaudit run examples/amorphous-dual-dedekind
proofaudit serve examples/amorphous-dual-dedekind --port 8766
```

The default runner is manual, so the first `run` command only creates four
review prompts. It does not spend model quota.

## Audit design

The panel has four required reviewers:

1. exact statements and logical deductions;
2. ZF/ZFA, Choice, ambient-model, and transfer assumptions;
3. permutation models, groups, filters, supports, and projective-type machinery;
4. adversarial counterexample and hidden-assumption search.

The gate requires all four to pass. Reviewer count is not hard-coded: edit,
remove, or add `[[reviewers]]` blocks and then run `proofaudit refresh`.

The PDF is segmented at page granularity, so some theorem-focused items overlap
on pages containing more than one result. The item title tells each reviewer
which result is in scope. This configuration is an audit plan, not an assertion
that the published paper contains an error.

