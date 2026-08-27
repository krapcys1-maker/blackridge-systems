# Production-readiness stage 1 review — 2026-08-27

## Verdict

The Blackridge orchestration and evidence core is working and has passed unit, integration,
security, packaging, benchmark, and clean-clone system checks. It is not yet honest to call the
complete scientific system production-ready. The grounded-researcher component has strong public
and adversarial evidence at L3, but no externally owned sealed holdout has been executed. The live
literature-search candidate PaperQA remains at L0 because independent supply-chain evidence found
unresolved license, vulnerability, posture, and provenance risks.

The system failed closed throughout this review: discovery did not imply approval, probes did not
fabricate verdicts, and unresolved evidence prevented promotion.

## Defects removed in this stage

| Commit | Change | Regression evidence |
|---|---|---|
| `5828df7` | Deterministic concurrent discovery ordering | Runner and workflow tests; both modules at 100% coverage |
| `ef8cea0` | CLI discovery, reporting, composition, evidence, and sandbox paths | Eight end-to-end CLI scenarios |
| `e57377a` | Bounded supply-chain command failures and expanded offline probe coverage | Nineteen supply-chain tests; module coverage raised above 80% |
| `965d2a5` | CI format, component compilation, and 70% coverage gates | Configuration regression test and full local reproduction |
| `b4b9093` | Windows-safe report output without replacement characters or truncated names | Real subprocess report inspection and encoding regression assertions |
| `b08aaa3` | Hash-bound, exact-inventory sealed holdout verification | Manifest, role, hash, size, revision, extra/missing file, and symlink tests |
| `d7cd2e4` | Reproducible semantic SBOM inventory fingerprints | Unit test plus three real PaperQA probe comparisons |

## Full regression result

- 160 tests passed and 3 Windows symlink-permission cases were skipped.
- Total statement coverage: 71.53%; enforced minimum: 70%.
- Ruff lint: pass.
- Ruff format check across `src`, `tests`, `tools`, and `components`: pass.
- Mypy: 27 source modules, no issues.
- Python bytecode compilation across `src`, `tools`, and `components`: pass.
- `pip check`: no broken requirements.
- `pip-audit --skip-editable`: no known vulnerabilities in installed dependencies; the local
  editable Blackridge distribution was explicitly skipped because it is not a published index
  artifact.
- Bandit medium/high gate: no issues. The remaining output is limited to reviewed low-severity
  findings and explicit temporary-directory suppressions.

## Live discovery exercise

The real GitHub discovery run for `literature-search` returned seven repositories. The provisional
top two were `Future-House/paper-qa` (78.71) and `danielnsilva/semanticscholar` (78.67). Scorecard
was unavailable for every result, and `allenai/s2orc` additionally required manual license/staleness
review. The generated blueprint selected PaperQA only as L0 and correctly set
`release_ready: false`.

- Discovery SHA-256:
  `6f8fc057f34d2625567f15d6731a402c34293f0d4ca319b2b6a7a1067344d87f`.
- Blueprint SHA-256:
  `e022e51044f03c48afa7afbfc2ac1e1a1df33672e161d2aebc2860fc1d5fe94e`.
- Artifact root:
  `D:\Skladacz aplikacji\blackridge-experiments\real-project-965d2a5`.

## Exact PaperQA supply-chain exercise

The latest probe inspected `Future-House/paper-qa` commit
`57e89f7223b0960d5ee5ea048c69e3c47e088572`, tree
`715a37b686963c1e7bdc5d50163f81cddf13124d`, and PyPI release `paper-qa==2026.8.12`.
The source checkout was pristine before and after the pinned Syft and OSV scanners. The required
`pypi-attestations==0.0.30` verifier was installed and detected.

Observed blockers:

- seven direct dependency license concerns, including `html2text` reported as
  GPL-3.0-or-later and six unknown, empty, or non-standard results;
- 23 vulnerable complete-lock-scope package entries, with maximum reported severity 9.8;
- OpenSSF Scorecard status `not-found`;
- missing PyPI integrity provenance for both the wheel and source distribution;
- incomplete declared-license coverage in the generated SBOM.

These are lock-scope observations, not a claim that every finding is production-reachable. They
require reachability and distribution review; they cannot be dismissed from the current evidence.
No PASS/FAIL verdict was assigned automatically, and PaperQA remains L0.

Latest evidence:

- Probe SHA-256:
  `71ce880ce7754f73661147705ff612ca34db3e7900e982f91a81231c48730ce9`.
- SPDX raw SHA-256:
  `85e784ce7f57b19ddbf337a175224361559c52cea6496ddd043c53a60d28c12e`.
- SPDX stable inventory SHA-256:
  `68eef1e10f84b099ec64190ba992377ab45e668704d362aab91b24ea2f11746d`.
- CycloneDX raw SHA-256:
  `d70086a3c8eb4832c82f74660835e58e2392fef370844897e15fff3a987e4b34`.
- CycloneDX stable inventory SHA-256:
  `e8fbb4d410ce960ca2c0b8968b1328dc7f4fde9554c103f59db794b9eeebaf3f`.
- Artifact root:
  `D:\Skladacz aplikacji\blackridge-experiments\real-project-d7cd2e4`.

Three real probes produced byte-identical OSV results and identical stable SPDX/CycloneDX
inventories. Raw SBOM SHA values changed only because Syft generated fresh timestamps and
document UUID/namespace values. The system now retains both the raw integrity hash and a stable
inventory hash, so this expected metadata variance no longer masquerades as a graph regression.

## Package result

The wheel and source distribution built in isolated environments and both passed `twine check`.

- Wheel SHA-256:
  `47e7ad830d5e5db478e8fd66a726a8f3d128e4f49f92cc4dbae79b7e444adf12`.
- Source distribution SHA-256:
  `379aa3dfbd7b43acda7f6ee6b09b7801ebae03e903386d2c422733d2f00055fd`.
- Artifact root:
  `D:\Skladacz aplikacji\blackridge-experiments\package-d7cd2e4`.

## Remaining gates and next action

1. Do not promote PaperQA. Classify the 23 OSV entries by runtime reachability and resolve the
   seven license concerns only if PaperQA remains the preferred candidate.
2. Probe the nearly tied `danielnsilva/semanticscholar` alternative at an exact commit and release.
   Compare its verified license, vulnerabilities, provenance, sandbox behavior, and contract fit
   against PaperQA before spending effort on PaperQA remediation.
3. Obtain an externally authored sealed holdout manifest and case set. The verifier is ready, but
   no blind evaluation can occur until an independent owner supplies the hidden suite.
4. Promote a literature-search component only after exact supply-chain review, networkless or
   allow-listed sandbox execution, contract tests, negative controls, and manual evidence review.

The immediate engineering next step is therefore the exact alternative-candidate experiment,
not a forced promotion of the current top-ranked repository.
