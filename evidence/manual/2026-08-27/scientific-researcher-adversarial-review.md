# Scientific researcher adversarial review — 2026-08-27

## Verdict

The adversarially hardened `grounded-researcher-v1` component passed all 162 frozen
adversarial checks and all 22 critical benchmark checks in three of three Blackridge
runs. The exact installed artifact was identical in every run. This is strong remediation
and replication evidence, but it is not a blinded holdout and does not justify L4.

## Failure discovery and generalized repair

The first frozen adversarial run against revision `104b826` matched 140 of 151 checks.
It exposed six classes of weakness: large distractor clusters could dominate raw graph
centrality; lexical bridges could pull the selection into an unrelated community; partial
evidence could be padded with unrelated documents; variable source minima inherited that
padding problem; booleans were accepted as Python integers; and duplicate document IDs were
not rejected by the typed request model.

The repair does not enumerate benchmark document IDs or topics. It:

- derives strong query-aligned seed documents relative to the peak direct score;
- permits graph expansion only from those strong seeds;
- discounts corpus-common link tokens and restricts centrality to aligned documents;
- restricts feedback terms to strong seeds;
- treats explicit evidence disclaimers as negative signals;
- rejects boolean minima and duplicate document identities with clean abstention;
- binds the public request model to `StrictInt`, so JSON Schema and typed validation agree.

The immutable repaired source is revision
`fc3b7705f620132f5c5ad866b75a66ab5cc9c775`, 319 physical lines, SHA-256
`c8b34ceaed8980bcb70a4a63c7afe713e9f4cdecec655f1a314d448086dfe56d`.

## Independent component evidence

- Standard L3 probe: four candidate cases passed every check; the green-exit broken control
  was rejected by ten semantic checks; no probe container remained.
- Standard probe SHA-256:
  `20a56229321de84f8efaebd172cf8369cab132759760416faed23244bf8f4fb9`.
- Standard review SHA-256:
  `d8eede51f0111b06673b7e0c1e4ea85bba1ee420a736b6fcb89fab614f658fec`.
- Adversarial probe: eleven cases and 162/162 checks passed, including corpus permutation,
  lexical bridge, negated near duplicate, partial evidence, minima 1/3/6, invalid inputs,
  duplicate IDs, and a 200-document resource case.
- The 200-document case selected the exact permitted twenty-document evidence community in
  0.432 seconds, below the eight-second bound.
- Adversarial probe SHA-256:
  `1de5089385799ffbb09a8c46da6bfd9331365ae04003ae67dcf9a8c608028475`.
- Adversarial review SHA-256:
  `8bddd5f58aecaafa089a0de30031989e11d1695fea87ab86253ca4ca09281b32`.
- The registry qualifier independently verifies both named reviews, their probe hashes,
  promotion bindings, contract hashes, source hash, and physical line count before reuse.

## Benchmark v6 (3 × 2)

The frozen evaluator is `scientific-researcher-v1` version 1.1. All paired control fields
matched, including model configuration, attempt number, builder budget, Docker image and
limits, evaluator inputs, schemas, case manifest, and telemetry source. Every immutable input
remained unchanged. All processes exited zero without timeout or output overflow, clean-install
checks passed, and no Blackridge container remained after the run.

| Arm | Task success | Critical checks | Mean builder wall | Mean generated lines | Mean reused lines | Total API tokens |
|---|---:|---:|---:|---:|---:|---:|
| from-scratch | 1/3 | 54/66 | 15.334 s | 195.3 | 0 | 11,946 |
| Blackridge hybrid | 3/3 | 66/66 | 5.127 s | 0 | 319 | 18,076 |

The two failed baseline attempts each matched 16/22 checks. Their functional output failed
status, source-count, citation, evidence-coverage, and required-concept checks. The third
baseline attempt passed 22/22. No automatic winner is fabricated for pairs whose baseline was
not experiment-eligible.

All three Blackridge workspaces contained byte-identical candidates with the registered
artifact SHA-256. Each functional output contained ten unique, exact corpus sources and ten
verbatim grounded citations; each negative output cleanly abstained with no claims or sources.
Manual inspection confirmed the complete stdout artifacts and every per-case check.

Artifact root:
`D:\Skladacz aplikacji\blackridge-experiments\scientific-researcher-v1-replication-20260827-v6-adversarial`

- Repository commit: `e8de5b0048d5ccd9b093b637b72b3a739f462546`.
- Manifest SHA-256: `d5cbc7a272906e537b16031b68929f7d9058481b8adfba7b7e7133a24b94d276`.
- Attempt 1 comparison SHA-256:
  `f8ac8b211ea9750c592faa0e073f85df47d9530d1a03333fd089205dfce12f5b`.
- Attempt 2 comparison SHA-256:
  `79e0c3138fc4037a5a886717d949968dd24b9ac2df7f255ed737414a149c07bd`.
- Attempt 3 comparison SHA-256:
  `57039d7bf4266157845da25253f298162a5b32770cd5816e8e192f5b31f79a3c`.

## Repository regression

- Pytest: 117 passed and 1 skipped.
- Coverage: 63.84%, above the enforced 60% gate.
- Ruff: all 53 scoped files are canonically formatted and the full lint gate passes.
- Mypy: all 26 source modules pass.
- Compileall: source, tools, components, and tests pass.
- `pip check`: no broken requirements.
- `pip-audit`: no known vulnerabilities in auditable dependencies; the local project itself is
  not published on PyPI and is explicitly reported as unaudited.
- Bandit CI gate (`-ll -ii`): no medium/high findings.
- Isolated sdist and wheel builds pass `twine check`.
- Wheel SHA-256:
  `16838e121011e067fa3a813ab639396471c04d2c90c019b2b02e1ad434edd1a7`.
- Sdist SHA-256:
  `1f346b53f1684b5f55ef63eac1bf5b54c80c0e2273f811342b6afeb4d9d9819d`.

## Limits and next falsifiable step

- The adversarial suite was built after inspecting earlier failures. It proves the stated
  remediation cases, not performance on unseen distributions.
- L3 covers exact reviewed contracts and fixtures, not universal semantic relevance.
- The hybrid arm reduced mean builder wall time by roughly two thirds, but sent the complete
  registered source in the builder prompt. It therefore used 51% more total API tokens than
  baseline. The installer should deliver verified bytes directly while the model receives only
  compact selection metadata.
- A separate evaluator owner should create a sealed, versioned holdout with unseen vocabulary,
  bridge structures, contradictory evidence, Unicode and boundary-sized inputs, and resource
  pressure. Run it once from a frozen revision and do not repair against that same holdout.
