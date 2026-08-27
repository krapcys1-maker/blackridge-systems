# Scientific researcher remediation review — 2026-08-27

## Verdict

The repaired Blackridge path works for the frozen `scientific-researcher-v1` evaluator:
three of three remediation runs passed all 22 critical checks. The result validates the
repair, but it is not a fresh blinded estimate of Blackridge's comparative advantage.

The component is promoted only to L3 (contract-tested). It is not L4 system-verified for
general production use.

## What failed before the repair

- The earlier replicated experiment produced task success 0/3 in both arms.
- The Docker harness originally omitted `--interactive`, so candidates received empty stdin;
  that orchestration defect was fixed in evaluator version 1.1.
- In remediation run v4, the model declined the L2 component in all three Blackridge attempts.
  Reuse remained honestly measured at zero. The repeated reason was that L2 did not prove the
  complete public contract, and the prompt's example encoded `selected_component_id: null`.
- Direct evaluation of that L2 component matched 20/22 critical checks. It selected one topical
  distractor instead of the tenth evidence document.

## Repairs

- Added lexical-community selection before per-document ranking. A query must reach a connected
  topic group large enough to satisfy `minimum_sources`; smaller distractor communities cannot
  fill a required source set.
- Added an independent 10-of-15 museum-loan fixture with a five-document gardening community.
  The component selects all ten loan records and excludes all five distractors.
- Expanded the isolated component probe to three answerable domains plus one unrelated negative
  corpus and validation against both frozen public JSON Schema contracts.
- Promoted exact artifact
  `092a3d7208ab0d983d95ceb51b8c8cd1c3fc45e6d71005d0893a6474d403b2c8`
  to L3 with a named, hash-bound review.
- Bound registry eligibility to the exact input and output contract SHA-256 values.
- Made reuse selection deterministic. The builder can supply supporting metadata, but cannot
  replace the eligible component. Installed reuse is measured from candidate bytes and physical
  lines, never from builder self-report.

## Independent component probe

- Repository revision: `efadac17efd630415c8c2bc862b8de55b4a9835e`
- Pinned image: `sha256:a03f1852c1c437df005ee33b01a26d5e55714c670d3e2273e007c56fd16a5903`
- Candidate: 4/4 cases with every check matched; 19 exact document-backed citations.
- Broken control: exit code zero in 4/4 cases, but 10 semantic checks rejected it.
- Cleanup: zero remaining probe containers.
- Probe SHA-256: `fb7b4ca52e26c572f871009e5f4586c884a086d83ee9a8db2017d902bac76b19`
- Review SHA-256: `1a7afdf1e2cf84ca522937325917c949c2ba247766f4b8b357f38d01d82deb8d`

## Remediation benchmark v5 (3 × 2)

Frozen controls matched in every pair: model, model configuration, attempt number, time budget,
Docker limits, evaluator inputs, schemas, cases, and measurement source. All inputs remained
unchanged and no benchmark containers remained.

| Arm | Task success | Critical checks | Mean builder wall | Mean generated lines | Mean reused lines | Total API tokens |
|---|---:|---:|---:|---:|---:|---:|
| from-scratch | 0/3 | 16/22 each | 16.778 s | 217.7 | 0 | 12,982 |
| Blackridge hybrid | 3/3 | 22/22 each | 6.055 s | 0 | 293 | 17,208 |

All three Blackridge workspaces contained the exact L3 artifact SHA-256. Each produced ten
unique grounded sources and ten exact citations in the functional case, then cleanly abstained
with zero claims and sources in the negative case. Clean-install probes passed 3/3.

The harness did not emit an automatic winner because the failing baseline runs were not
experiment-eligible. No winner is retroactively fabricated.

Artifact root:
`D:\Skladacz aplikacji\blackridge-experiments\scientific-researcher-v1-replication-20260827-v5-remediation`

- Manifest SHA-256: `9af1eff31961ad17614d73fa2f3d062c5fc86455229e7bf7fd8609b344a6a32d`
- Attempt 1 comparison SHA-256: `e6ab938279764c762be4966b626abbcbe20b23569aa72c822c0b8935e2d40d5d`
- Attempt 2 comparison SHA-256: `2ef62f09c8fd27c6632c5afcbc7c61c658fc769317fc4e337b40ab9db0752391`
- Attempt 3 comparison SHA-256: `d0dc4507601b1fa61e1bd36bee0bc2f105a90c0bc4f67e86189abd9578289f1c`

## Regression evidence

- Pytest: 104 passed, 1 skipped.
- Coverage: 63.81%, above the enforced 60% gate.
- Ruff lint: pass; changed files also pass Ruff format.
- Mypy: pass for all 26 source modules.
- Compileall: pass for source, tools, components, and tests.
- `pip check`: no broken requirements.
- `pip-audit`: no known vulnerabilities in auditable dependencies; the local project is not on
  PyPI and is explicitly reported as unaudited.
- Bandit CI gate (`-ll -ii`): no medium/high findings.

## Limits and next falsifiable step

- The reviewer and component author had already seen the frozen evaluator failure before v5.
  Therefore v5 is remediation validation, not a blinded benchmark suitable for publication.
- L3 covers the reviewed contracts and fixtures, not universal semantic relevance.
- Repository coverage remains 63.81%; CLI, sandbox, supply-chain, and workflow paths contain the
  largest uncovered regions.
- The hybrid prompt uses more tokens because it embeds the source. A registry API should expose
  verified selection metadata and install bytes directly without sending the full source to the
  model.
- A separate evaluator owner should create a sealed, versioned holdout with unseen domains,
  adversarial bridge documents, near-duplicate distractors, varying source minima, malformed
  inputs, and resource-pressure cases. Run it once from a frozen revision and do not repair against
  that same holdout. Only a complete pass plus named end-system review should support L4 promotion.
