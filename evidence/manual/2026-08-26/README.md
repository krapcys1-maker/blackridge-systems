# Manual verification — 2026-08-26

Environment: Windows, Python 3.12.10, Blackridge editable install. External observations are a
time-stamped snapshot and may change upstream.

## Segment verdicts

| Segment and scenario | Verdict | Manual observation |
| --- | --- | --- |
| capability contract schema | PASS | Six self-hosting capabilities and eight acceptance scenarios loaded. A deliberately duplicated scenario ID was rejected. |
| deps.dev / `paper-qa-package-evidence` | PASS with blocker outside this scenario | `paper-qa@2026.8.12` resolved to 75 nodes and 16 direct dependencies. deps.dev reported `non-standard`; GitHub reported `Apache-2.0`, so package approval remains blocked pending license reconciliation. |
| deps.dev negative package and version inputs | PASS | A nonexistent package and nonexistent requested version both exited 2 while retaining explicit failure evidence without a verdict. |
| exact-version guard / `exact-version-not-substituted` | PASS | The failed GROBID 0.9.1 lookup retained the exact input and error and did not substitute another package version. |
| deps.dev Maven coverage for GROBID 0.9.1 | FAIL as a source of the requested release | Exact 0.9.1 was unavailable. An unpinned query selected the only indexed version, 0.3.4 from 2015, while GitHub reports release 0.9.1 from 2026-08-04. |
| repository discovery / `known-sandbox-query` | PASS for discovery, not selection | The corrected query returned SWE-ReX and other real sandbox implementations. The top metadata score belonged to a broader agent framework, proving that L0 ranking is only an inspection queue. |
| original discovery contract | FAIL | The original YAML scenario described sandbox discovery but issued the unrelated query `code search agent`. The failed artifact was retained and the query corrected before rerunning. |
| OpenSSF Scorecard observation | PASS after defect correction | The first run silently stored seven missing scores. The corrected run retains seven explicit `not-found` warnings. A positive control against `ossf/scorecard` returned status `available` and score 9.0. |
| manual review gate negative cases | PASS | Missing `--observed` and an unknown scenario both exited 2 and wrote no review file. |
| provisional blueprint gate | PASS | The artifact stays `release_ready: false`, evidence L0, requires L2, and warns that the leading candidate still needs inspection and sandbox boot. |
| environment construction / `python-repository-clean-boot` | PASS | SWE-ReX 1.4.0 ran `pypa/sampleproject` at exact commit `621e497...`. The real unittest and independent `41 -> 42` JSON artifact matched; host hashes matched and the container was removed. |
| sandbox negative boundary / `sandbox-failure-is-evidence` | PASS | The wrong expectation retained exit 1 and `expected 999, got 6`; the following sentinel was explicitly not run, the host stayed unchanged, and the container was removed. |
| component adaptation / `schema-mismatch-adapter` | PASS | The unadapted fixture failed because `document` was absent. The RFC 6902 adapter copied `paper.title` to `document.name`, passed Draft 2020-12 validation, and preserved every source value. |
| system verification / `deliberate-negative-case` | PASS | Working and one-operation-broken compositions used identical input and schema. Both patches executed without an exception, but artifact validation accepted only the working output and rejected missing `document.name`. |
| complete supply-chain evaluation / `known-repository-review` | PASS for evidence behavior; candidate BLOCKED | The exact PaperQA commit was inspected independently with GitHub, deps.dev, Scorecard, Syft, OSV-Scanner, and PyPI Integrity. Missing posture, license coverage, provenance, and vulnerability findings remained explicit, so the release was not approved. |
| built-wheel installation | PASS after defect correction | The first build failed because the dev environment did not declare `build`. After correcting it, the wheel contents were inspected and a clean venv loaded the exact experiment, exposed the CLI options, and passed dependency checks. A polluted system-site control still failed and was retained. |
| benchmark calibration / `green-broken-artifact-detected` | PASS for harness calibration; A/B NOT RUN | The reference matched 19/19 critical checks and excluded all distractors. Both broken processes exited zero and emitted schema-valid JSON, but the artifact matched only 9/19; ten concrete grounding, source, relevance, coverage, concept, and abstention boundaries were detected. |
| benchmark comparison guard / `mismatched-comparison-controls-refused` | PASS | Changing only `model_identifier` stopped comparison before either candidate was evaluated, retained the named mismatch, and created no task result or winner. A matching plumbing control ran both arms and still assigned no automatic winner. |
| public benchmark schema / `public-output-contract-enforced` | PASS | Both schema-negative processes exited zero, but the exact published Draft 2020-12 schema rejected `unchecked_payload`. No deeper correctness checks were fabricated after contract failure. |
| composer / `compatible-route-generates-and-runs` | PASS for calibration; release NOT READY | The solver rejected a policy-blocked sink, selected two eligible fixtures and exactly one adapter, independently rehashed all 15 files named by provenance, and executed three contract-valid steps to the expected report. |
| composer / `missing-edge-stops-generation` | PASS | Removing the only adapter left `report-sink` unresolved. The CLI exited nonzero, generation and runtime stayed null, and no system directory was created. |
| composer / `green-exit-invalid-artifact-stops-runtime` | PASS | The source and adapter passed. The deliberately broken sink exited zero, but its retained JSON lacked `report.title` and `trace.source`; two schema errors stopped the runtime. |
| composer / `unreviewed-production-evidence-refused` | PASS | An immutable command file matched its hash, but its self-declared L3 had no named review. Production solving exited nonzero without generation or execution. |

## Important findings

1. Package and repository license metadata can disagree. Both facts must remain visible.
2. Registry coverage can be stale even for an actively released upstream project.
3. Search relevance plus popularity can rank a broad framework above a more precise component.
4. A missing Scorecard is not a score of zero and not an API success; it needs an explicit state.
5. An exit code proves only that a command completed, not that the produced artifact is correct.
6. SWE-ReX 1.4.0 omits its Docker client's `aiohttp` dependency and otherwise falls back to an
   unpinned `pipx run swe-rex`; Blackridge must supply both the dependency and pinned server image.
7. The effective container boundary was observed as zero Linux capabilities, `NoNewPrivs=1`,
   1 GiB memory, 256 processes, and two CPUs. This is not a hardened multi-tenant sandbox claim.
8. `jsonpatch` v1.33 mutated the empty object held in its working `add` operation when a later
   `copy` wrote beneath it. Passing a deep copy prevents the declarative definition and retained
   request from changing; the upstream working-copy mutation remains an explicit observation.
9. Both the working and broken patches applied without an exception. Only inspection of the output
   against its consumer schema caught the missing connection, exactly as the manual-test policy
   requires.
10. A comparison using a different target contract/schema exited 2 and retained failure evidence;
    the verifier does not draw a negative-control conclusion from different workloads.
11. The exact PaperQA repository commit and Apache-2.0 LICENSE were confirmed, but 7 of 16 direct
    dependencies had empty, non-standard, or GPL-family license results. All 305 SPDX package
    entries had `licenseDeclared: NOASSERTION`; an SBOM is not proof of license clearance.
12. Scorecard returned explicit `not-found`, independently of OSV-Scanner. OSV exited 1 and found
    23 vulnerable package entries, 223 primary advisories, and maximum reported severity 9.8 in the
    complete lockfile scope. Optional/development reachability is not yet classified.
13. Both PyPI distribution files returned HTTP 404 from the Integrity API with no attestation
    bundles. GitHub's valid commit signature was retained separately and was not misrepresented as
    package provenance.
14. A first manual Syft invocation exited 0 while logging a failed network update check. The final
    probe disabled update checks explicitly and ran Syft offline; command success alone was again
    insufficient evidence.
15. Evidence files use canonical LF bytes and are marked `-text` in `.gitattributes`. This prevents
    Windows checkout conversion from invalidating the recorded hashes; review digests were updated
    once during that content-preserving normalization and then re-audited against staged Git blobs.
16. The frozen benchmark reference matched all 19 critical checks. Its bounded answer used exactly
    10 unique support sources, excluded all five distractors, and retained 10 claims whose cited
    quotes were independently found in the named documents.
17. The broken candidate returned exit 0 and schema-valid JSON for both cases. Artifact inspection
    still detected a fabricated paper, insufficient source count, uncited and ungrounded claims,
    missing concepts/evidence, and a false answer where clean abstention was required.
18. The comparison layer refuses unequal model controls before candidate evaluation. With matching
    plumbing fixtures it retains both raw arms and their delta, but `automatic_winner` and weighted
    success score remain null. No real from-scratch versus Blackridge run has happened yet.
19. The evaluator now hashes and executes the same input/output JSON Schemas published to builders,
    and hashes its own module bytes. A schema-negative green process was rejected in both cases;
    evaluation stopped at the contract boundary instead of inferring deeper correctness.
20. The compatibility solver uses hard gates and selected one three-step path: research source,
    exact RFC 6902 adapter, and report sink. The lower-priority number on a deliberately blocked
    alternative could not override its policy blocker.
21. Both command files and the canonical adapter operations matched their locked SHA-256 values.
    The runner independently matched every file named by each generated provenance manifest before
    executing either component.
22. Removing the sole contract edge left eligible components but no route from `paper-record/v1`
    to `document-record/v1`; generation and execution were not attempted and no directory appeared.
23. The negative sink exited zero and emitted parseable JSON. Draft 2020-12 validation still
    rejected missing `report.title` and `trace.source`, proving the runner inspects the artifact.
24. Composer v1 is deliberately single-stream and host execution is calibration-only. Production
    mode requires L3 named reviews whose raw probe hashes still match, and production execution
    remains behind the sandbox boundary. Both generated fixtures stay `release_ready: false`.
25. An explicit production control proved that immutable provenance is necessary but insufficient:
    the command hash matched while its self-declared L3 was rejected for lacking a named review.

## Retained artifacts

- `paper-qa-depsdev-probe.json` — raw real-world package evidence.
- `paper-qa-depsdev-review.json` — named manual review of the PaperQA scenario.
- `nonexistent-package-probe.json` and `invalid-version-probe.json` — retained failures.
- `grobid-0.9.1-unavailable-probe.json` — retained exact-version failure.
- `grobid-core-depsdev-probe.json` — evidence of the stale Maven coverage result.
- `grobid-version-guard-review.json` — named review of the no-substitution behavior.
- `repository-discovery-invalid-query.json` — failed first discovery contract.
- `repository-discovery-scorecard-silent.json` — run that exposed missing warnings.
- `repository-discovery-run.json` — corrected discovery and explicit warnings.
- `repository-discovery-blueprint.yaml` — provisional L0 blueprint.
- `sampleproject-sandbox-positive-probe.json` — raw pinned-repository build, test, artifact,
  host-integrity, runtime-boundary, and cleanup observations.
- `sampleproject-sandbox-positive-review.json` — named review of the clean-boot scenario.
- `sampleproject-sandbox-negative-probe.json` — retained assertion failure and skipped sentinel.
- `sampleproject-sandbox-negative-review.json` — named review of failure retention.
- `swerex-1.4.0-integration-defects.txt` — fresh-environment reproduction of the missing
  `aiohttp` declaration and source-inspected unpinned fallback.
- `adapter-mutated-definition-defect-probe.json` — retained first adapter run that exposed a
  mutated patch definition in the evidence itself.
- `adapter-paper-title-positive-invalid-source-probe.json` — corrected deep-copy run retained after
  a source tag URL typo was found; it is superseded and has no active review.
- `adapter-paper-title-positive-probe.json` and `adapter-paper-title-positive-review.json` — final
  source-corrected before/after contract evidence and named review.
- `adapter-paper-title-broken-probe.json` — early negative adapter result retained before the paired
  workload was normalized.
- `composition-working-vs-broken-probe.json` and its review — identical workload, one removed copy
  operation, complete output artifacts, and the detected target-contract failure.
- `composition-mismatched-workload-failure.json` — retained refusal to compare different target
  contracts and schemas.
- `paperqa-supply-chain-probe.json` and `paperqa-supply-chain-review.json` — exact-commit legal,
  posture, SBOM, vulnerability, and provenance evidence plus its named manual review.
- `paperqa-supply-chain-artifacts/` — complete SPDX JSON, CycloneDX JSON, and OSV JSON outputs;
  these are retained rather than replacing the detailed findings with a summary.
- `packaging-build-missing-tool.txt` — failed first wheel build, corrected content inspection, clean
  installation control, and the separately retained polluted-environment failure.
- `benchmark-reference-probe.json`, `benchmark-broken-probe.json`, and
  `benchmark-calibration-probe.json` — complete stdout, parsed artifacts, objective checks, frozen
  input hashes, and paired calibration observations.
- `benchmark-calibration-review.json` — named review that passes only the evaluator calibration,
  explicitly not either A/B method.
- `benchmark-comparator-control-probe.json` — matching comparator plumbing fixtures with both raw
  arms, no weighted score, and no automatic winner.
- `benchmark-comparator-mismatched-controls-failure.json` and its review — retained pre-execution
  refusal when only the model identity differs.
- `benchmark-schema-invalid-probe.json` and its review — green-exit unexpected-field control proving
  that the exact public Draft 2020-12 output contract is executed before deeper artifact checks.
- `composer-positive-probe.json` and its review — complete qualifications, selected route,
  generated hashes, command observations, intermediate artifacts, and final report inspection.
- `composer-no-adapter-probe.json` and its review — retained incomplete route with no generated
  directory or component execution.
- `composer-broken-output-probe.json` and its review — green-exit invalid final JSON plus both exact
  Draft 2020-12 errors.
- `composer-unreviewed-production-probe.json` and its review — matching immutable command bytes but
  rejected self-declared L3, with no generated directory or host execution.
- `composer-positive-system/` and `composer-broken-output-system/` — complete generated bundles.
  Each `provenance.json` retains hashes for the other 15 files, including exact definition and plan
  copies; both manifests were independently recomputed with zero mismatches.

SHA-256 checksums of the canonical bytes retained in Git:

```text
grobid-0.9.1-unavailable-probe.json                 9d2f13a29efdb6f8e4723f15b839497d6bccd6704a03da1d843dc263c927b1ea
grobid-core-depsdev-probe.json                      33b6b94890efe5f75f57d896894c9c207490a7a6fe0ca796550f320c4e091b17
grobid-version-guard-review.json                    ba41f4c3b5c1cb6d9898be83a70750f6362e98a153a408b5664fc950a40cf2e3
invalid-version-probe.json                          5125073f31e8b2ef136d5018ac447bf89b331a5fd80f2c4fa56c37a6002c56d5
nonexistent-package-probe.json                      17cfa3b3e4210c04501ff6d3fe90afcbdebe4644491c7bebddf226551dfb4662
paper-qa-depsdev-probe.json                         cfb4c1de5c549bc5cadf8c68283ede639642b28509638b7082738c2963f040b5
paper-qa-depsdev-review.json                        65922e8cc36081060615218ee9583301319a0f06087205c56b70e11e37357c78
repository-discovery-blueprint.yaml                 fba9f188e6ed2e19da132fc2e1824b8d4b9698b2ccd58fde0509b786fea85961
repository-discovery-invalid-query.json             cfbf2ea3538f825662d5ea18d0528d12182f52f0624a019fd3b970ecdd5a9f68
repository-discovery-run.json                       c49d98209847426b0062e9a180c3851a512dcad2fd0262c8046fc04ff2f5341f
repository-discovery-scorecard-silent.json          111eff1ed232f511a1da1bd4f1f85b401a6f03978c20cca3a8412810fbf7e747
sampleproject-sandbox-negative-probe.json           5bc6629c81cddd05063fe491e7ffbb25a1ba39ac397f5ccfd61f457d19aa4a34
sampleproject-sandbox-negative-review.json          3b8900169ea7bd9c980432afdf3e0d68d6adcf5e1f34b2521db13488f5ef26fd
sampleproject-sandbox-positive-probe.json           1bb68ace709d368286c55f9b28e8ce6ed33e76328775e5d82d5763a614a71392
sampleproject-sandbox-positive-review.json          e877601bdc1cbb84359c69c1382a71ed00b489d2f4d35420e9346755b36190f0
swerex-1.4.0-integration-defects.txt                73b791a1d107d6e99e437c08f6efa5899908b32dbf7b2fc6cee4aca3cd5a9567
adapter-mutated-definition-defect-probe.json        e9109acf3986e104993b5a9b368bafc476477260c559568d69d69af7158f9e30
adapter-paper-title-broken-probe.json               7a3191d482fb30e302a6d0765bbc14520486837e3317b9ae06610a555bb25ee0
adapter-paper-title-positive-invalid-source-probe.json 521880c3fb0bdb69cffca160f83919a1d7ff4279eb3cfdc77cf311375b7dc314
adapter-paper-title-positive-probe.json             bffffadfc35893de0bbfd1207be43df9aa0a488536397ccd91460e3dee5bb6e7
adapter-paper-title-positive-review.json            6098609d72e47300bcc57fbba001e765418171c4280a3252f0cf996fd8e1bc3e
composition-working-vs-broken-probe.json            5d7c2c1252b81b426d94eebd78265443f650fa4304d8b311108c378b46f85b21
composition-working-vs-broken-review.json           0ea98401eb4c10774ac4d36e7a0d1eeb7b797063e52a39bd637c77aef92ccde0
composition-mismatched-workload-failure.json        a04804393c0bd9f604efdb33b782d1e59565d6617baafd44c354fcf3af293f77
paperqa-supply-chain-probe.json                     9d4a7d38221e423a050a66fe5ed3b4d340ade3d5e931547ef6df74b251a3b5de
paperqa-supply-chain-review.json                    daf0a12fbe1b9e7aae058a21200e53f074920446dbfeb1f1b418a7cfa2744a5f
paperqa-2026-08-12-supply-chain.spdx.json           f7dc8d67b98c121554e169ac37d9184e9a8cd5ac1227402950ba2a18b31a8265
paperqa-2026-08-12-supply-chain.cdx.json            56fc40d40fd0e8789f30237863e46f6ae79d2f1d2a604ba68973a47b10624cd4
paperqa-2026-08-12-supply-chain.osv.json            8d7582f0cb39e6c4def0cbcecda5ad13a9a2457a1a3e707948de6045017ab1ac
packaging-build-missing-tool.txt                    d7fd652eb917ecc0c55ba45b7a0fc7e15085e6aa0359556973b701bda7c82bfa
benchmark-broken-probe.json                         1ab99a2ff8d99d40c3c3bdd3e506342d4c5335b4c157a647b23473515fcb9248
benchmark-calibration-probe.json                    3d7068b555df1f832fdf1b9820ae079dde04d1380d31f5ecfb81fb6c3f3fa5a6
benchmark-calibration-review.json                   a40a03b2b90e5bfad6f8c9982f536e96d1e0b0818494b4f9dc914a0a68d3efe2
benchmark-comparator-control-probe.json             ebc210d38245c0b6ac9ab0d98c7b4e147bb78a79c9016fea05b128208b427a82
benchmark-comparator-mismatched-controls-failure.json ff221bd20f364085aeabd2ebe6788f87d567132a5d51ecd221fa9c47d9830bd9
benchmark-comparator-mismatched-controls-review.json cdad80c87f789931bd4b4c6d7a36b236d0c98ff6a0492900359303e695c166b3
benchmark-reference-probe.json                      6ada3794550931671ca7af9726d77ed0782a6363303c5ac8eee6f0af8ff67244
benchmark-schema-invalid-probe.json                 adb0957abfb15f67345d6b6843d1323566625ffc2dce83ae5c0502efda6f5173
benchmark-schema-invalid-review.json                806357dab86180b7a7fe5c48b1da0ee5106cfe8fa036e012be7a0ece92e3541e
composer-broken-output-probe.json                   b5a3d2ef487e05d834c827d5d3304a71b0300e817b5a8a8bfc3f9c75bea02c11
composer-broken-output-review.json                  875d22de9f0ba4287f712bb76d5e838a69d0ceb8e2d4304cbd68244447714023
composer-no-adapter-probe.json                      6e24777e39a6b7ffc5b438283e8efba798c0c2a89028964c22218e688e1caa32
composer-no-adapter-review.json                     e6b2e129b71657d63e987e7db48f233a6e4710b46060b4b98e13bab45fee5672
composer-positive-probe.json                        5ab51701feef1a142c661086a9bf9ecbb60ae95004a419ca70198bec20305bd3
composer-positive-review.json                       67e129dba7830e5779586af25b951c44f94069c763f4db3cbf44530a9a001bf4
composer-unreviewed-production-probe.json           0a22c4d5c488d253828e0b5a6d997c7580f04af96d80c7a2f5227ad68a6c68d9
composer-unreviewed-production-review.json          37d70e6b426a03c05886ec575048db862283bb8606cdbf855b40d7fd0d4d3f1d
```
