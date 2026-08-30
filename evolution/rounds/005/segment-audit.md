# Round 005 segment audit

Champion baseline: v1.4. Baseline verification on 2026-08-29: 240 passed, 3 skipped, 73% total
coverage. The skipped cases require Windows symlink privileges and must be exercised in Linux CI.

| Segment | Principal modules | Existing project evidence | Baseline risk | Round-005 action |
|---|---|---|---|---|
| Planning and discovery | `planning`, `workflow`, `github`, `octocode`, `ranking` | Duplicate Finder discovery and system E2E | Medium: external-service partial failures | Retain; add multi-workload comparison before changing ranking |
| Supply chain and reuse | `supply_chain`, `depsdev`, `git_integrity` | Semantic Scholar, SciFact, package probes | High impact | Mandatory provenance and immutable-revision gate for every winner |
| Gap generation and repair | `generation`, `operator` | Duplicate Finder 3/3 on v1.4 | High: stochastic and overfit-prone | Concrete-test binding plus hash-bound repair diversity in candidate A |
| Composition and adaptation | `composition`, `adaptation`, `composition_evidence` | Grounded Researcher, SciFact, wheel auditor, system E2E | High: largest module and broad runtime surface | Cross-project deterministic lane plus Docker E2E |
| Sandbox and process boundary | `sandbox`, `runner`, `process_boundary` | Networkless non-root Docker E2E | Critical; unit coverage is 47% | Treat Docker fail-closed E2E as critical, never compensate with score |
| Benchmark and evolution | `benchmark`, `holdout`, `evolution` | Scientific Researcher A/B and four historical rounds | High: prior winner rule was applied partly outside selection model | Freeze one multi-workload selection order and retain exact ties |
| Provenance and release | `provenance`, `release_compliance`, `quality` | wheel/sdist, notices, SBOM, installed-wheel E2E | Critical; provenance unit coverage is 50% | Run from clean clone and isolated environment for every promotable candidate |
| CLI and packaging | `cli`, `io`, package metadata | installed `blackridge` commands and system E2E | Medium; direct unit coverage is 33% | Judge via installed-wheel workflows, not line coverage alone |

## Existing systems built or assembled on the engine

1. Duplicate Finder: generated gap project; tests generation, repair, public evaluator, and hidden
   execution boundary.
2. Scientific Researcher: controlled from-scratch versus Blackridge hybrid builder experiment;
   tests verified component reuse, grounded synthesis, citation integrity, and abstention.
3. Grounded Researcher: reusable evidence-grounded component with adversarial identity, quoting,
   ordering, and insufficiency cases.
4. Scientific Claim Auditor: SciFact-based retrieval and audit composition with exact quote and
   corpus-relative verdict controls.
5. Wheel Release Auditor: wheel inventory plus release-policy composition covering RECORD,
   licenses, unsafe paths, and dependency policy.
6. Generic linear composition calibration: installed-wheel system E2E covering portable bundles,
   provenance roots, networkless sandboxing, cleanup, and fail-closed behavior.

## Findings that constrain promotion

- v1.4 is the measured champion, but its new test-only path did not cause any of its three passing
  attempts; causal benefit remains unproved.
- A+B safely exercised that path but returned the same failing test suite three times. Repetition
  must be visible, hash-bound, and rejected before another sandbox run.
- A+B contains a deterministic concrete-test binding absent from v1.4. This is a safe transfer
  candidate because it rejects false acceptance evidence before execution.
- The v2 ledger base still replaces mature generation and release gates with a separate foundry.
  Its potentially useful append-only evidence semantics should be transferred narrowly; the base
  cannot be promoted until it passes the same packaging, provenance, sandbox, and workload gates.
- The old Duplicate Finder public and independent evaluator hashes are identical. That preserves
  execution separation but is not a hidden behavioral holdout. Round 005 therefore adds unrelated
  scientific, supply-chain, and runtime workloads to reduce task-specific overfitting.
