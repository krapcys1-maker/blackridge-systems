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
| source provenance / `exact-source-history` | PASS within the frozen scan scope | All 22 tracked Python files have SHA-256 and first-add history. Four requested commits matched their checkouts, 1,133 upstream files were scanned, and no exact normalized six-line fragment matched. The probe explicitly does not call that proof of originality. |
| provenance gate / `empty-copy-registry` | PASS | The current registry contains no copied/adapted source, no attribution marker was found, and the gate reported zero issues while retaining the need for a separate similarity scan. |
| provenance gate / `incomplete-copy-control` | PASS | A deliberately unsafe copy record exited 1 with 11 concrete issues, including mutable revision, missing source paths, license, attribution, modifications, review, and destination. Copy remained disallowed. |
| wheel release / `exact-wheel-bundle` | PASS for the wheel evidence; no legal verdict | An isolated build produced a wheel with 29 members, seven runtime and seven optional declarations, and all three Blackridge legal files. Pinned Syft generated both SBOMs; a second clean venv installed and imported the exact wheel. |
| image release / `exact-image-block` | PASS for the blocking mechanism; IMAGE BLOCKED | The exact runnable image yielded 161 SBOM packages, 35 Python distributions, 118 Debian packages, and 161 readable bundled license files. Four unresolved obligation classes kept `release_gate_open: false` and the CLI exited 1. |

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
26. Git history inspection covered every one of the 22 tracked `src/blackridge/*.py` files. The
    exact-fragment comparison scanned 1,133 files at four frozen upstream commits and found no
    normalized six-line match. This does not detect renamed, reordered, translated, or heavily
    edited source and is not an originality guarantee.
27. The copy gate's deliberately incomplete record produced 11 separate causes instead of one
    generic failure. An empty no-copy registry passed only because no derived-code markers were
    present; marker-free copying remains outside that gate and inside the similarity/manual review.
28. The final wheel contains `LICENSE`, `NOTICE`, and generated `THIRD_PARTY_NOTICES.md` as PEP 639
    license files. Its seven runtime requirements exactly matched the active manifest; seven extras
    were retained separately rather than misclassified as embedded dependencies.
29. A no-dependencies install correctly could not import the compliance module because PyYAML was
    absent. Installing the exact wheel with its declared dependencies in the clean venv succeeded,
    imported `blackridge.release_compliance`, and ran the installed notice consistency check.
30. The exact image is functional but not publishable. SWE-ReX 1.4.0 and aiohttp 3.14.3 ran with
    networking disabled, and all Blackridge legal files were present, while the compliance CLI
    still exited 1 because functionality is not license clearance.
31. Image inspection found four Python packages with missing/unknown metadata (`markdown-it-py`,
    `mdurl`, `ptyprocess`, and `swe-rex`). Their extracted texts respectively show MIT, MIT, ISC,
    and MIT terms, but no reviewed override is yet part of the release policy. `bashlex` reports
    GPLv3+ and `certifi` MPL-2.0, so both remain explicit review targets.
32. Every one of the 35 Python distributions had at least one extracted license/notice file, and
    every one of the 118 Debian packages had an extracted `copyright` file. That still does not
    satisfy corresponding-source obligations: no reviewed source archive/offer mechanism exists,
    and apt plus transitive pip resolution is not completely locked.
33. The first compliance build exposed that the repository had no explicit `.dockerignore`.
    A fail-closed allowlist now sends only `LICENSE`, `NOTICE`, and `THIRD_PARTY_NOTICES.md`;
    BuildKit reported a 97-byte context. The final scanned image was rebuilt through that boundary,
    so `.env`, Git history, source, and retained evidence were outside the build context.

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
- `source-provenance-probe.json` and `source-provenance-review.json` — file-by-file SHA/history,
  exact upstream checkout identities, 1,133-file comparison scope, limitations, and named review.
- `provenance-gate-positive-*` and `provenance-gate-incomplete-copy-*` — paired no-copy baseline
  and deliberately incomplete copy control with their named reviews.
- `release-wheel/` and `release-wheel-review.json` — exact wheel manifest, SPDX, CycloneDX,
  license bundle, checksums, commands, and clean-install manual verdict.
- `release-image/` and `release-image-blocked-review.json` — exact image manifest, complete SBOMs,
  Python and Debian inventories, source-package mapping, license bundle, blockers, and named review
  that passes the blocking behavior while explicitly leaving the image unapproved.
- `docker-context-control.txt` — exact build/runtime commands and observed 97-byte allowlisted
  context, retained separately from the legal release verdict.

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
docker-context-control.txt                          a9fee655e92cb91ea90f8e58a3ba0d02e0605c17fb7050f2942bb75c4c33f3ee
provenance-gate-incomplete-copy-probe.json           234b21eb02b14e50f9ea4bc9fa9bb13544f0d679b7c3628a94e9a960f9af9289
provenance-gate-incomplete-copy-review.json          d045b9b45347ef5046efa569ae0b6af74fbc9ff5786e6a82a7ca876e52171f4d
provenance-gate-positive-probe.json                  471174a2853b6b934ef2e8d6fc6b33e1ad5d3e61b9ac2d563bca54f29d0d85bf
provenance-gate-positive-review.json                 aa591b8565bc092a017ac1d0ef146bc033d48c122e941d9d95ce19e575461b85
release-image-blocked-review.json                    20e6ffc4e5e7d3e330d845b7d27ce4167527e5b57e2731d3394248c23fcbaaa7
release-image/image-components.json                 6f4e3fbab7d2246bd667acab12c041c849a4ed46224f6b35f13056d8440d8822
release-image/license-bundle.zip                     cb0d8fbfe42f9edae1bd85a683137b53c2b24511a4de3d456880932e38fdeade
release-image/os-packages.tsv                        a00c8e8c765ea2771d2416f0ab54a61a49b09139de10910c081dc0f1d918e421
release-image/os-source-manifest.json                cf0684abb94683ebf992c67fae05932d375abb7e147c59daf7708fd1e6901812
release-image/probe.json                             7e83752686a05e37d66fbb4ae58837eb01e1810b4f0bc901a0fb72c93d04f9fd
release-image/python-packages.json                   978dcaa36c9de82827587e08ccb9461ecf29d27984da76362c3069648798ab83
release-image/sbom.cdx.json                          ac2c5f332d75d30d593960f65fb35f9b56108119a25f70e33d95ee1b4cf31989
release-image/sbom.spdx.json                         4a3c2184a9d291ed182accd47624d64236c9f4ebdd108c2fc4be96afc89a7124
release-wheel-review.json                            47796eb66219e2dc1a08946c693ea074a42214b9f33a1c1b73d4fc01966d07c0
release-wheel/license-bundle.zip                     f54c7f6135cbce64837ecd43e49056cd642bfc971be78681e912dbb582386798
release-wheel/probe.json                             713bd9009ee9e08b7c9ca651733ec776f2830f2fc147497a8c833707d1f8b3cd
release-wheel/sbom.cdx.json                          d289520f1f8e649dc8fc1074f39c89600d3ea5b6f0dd35a74ab67a490f8a0a6c
release-wheel/sbom.spdx.json                         cb0a19e67e208c371b94576b63f0f2b174c6846fa7be945028423689f56cbd60
release-wheel/wheel-components.json                  e4f6e5e44f713e59367769f48e8f5c6891b3db6538400ccc5a577354139f637c
source-provenance-probe.json                         add14b7794030cfc3095d61e1d38b44a79e57ec40c471e1aa2d63f6208ef7dc3
source-provenance-review.json                        e1ef5a2ebeb178186e0939f1fa0711839c030e9d234a4028b22e0c3f49cd60ea
```
