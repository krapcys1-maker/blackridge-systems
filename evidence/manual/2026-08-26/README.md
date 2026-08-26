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
| complete supply-chain evaluation / `known-repository-review` | NOT RUN | Scorecard missing-state behavior was tested, but exact-commit OSV, SBOM, provenance, and complete license evidence have not all run together. |

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

SHA-256 checksums at creation time:

```text
grobid-0.9.1-unavailable-probe.json         2ea7dfb1fe53366c2daeb9330af85144bf2efd77b40aab2e5a9a5ace352946a5
grobid-core-depsdev-probe.json              1a61e534497c27aa4ba3e7f2649ad5c6001a8ebe2b9b1acbb797a7e74b65358d
grobid-version-guard-review.json            eef3c87b272ff4da6654edbb665061f1b2fef189869640791b47315e47ac0cdd
invalid-version-probe.json                  1a578a1add11a6c0fde4ae2176c554c10c1844c77b5843712116accecd2368a5
nonexistent-package-probe.json              982e9724e8b772a78dcd28b5b9403b9835e326276541f9b266e41bb34f8d970b
paper-qa-depsdev-probe.json                 fde64437cf5464d514f13c488689246af90ce77197123105edb7bacb722ada03
paper-qa-depsdev-review.json                acda4c595c3a2ada0f416768274f86f6c0ff009719533fb0fe55937ffa3b8352
repository-discovery-blueprint.yaml         89165e8062034263bc081a518177724024dd72a47b9311310faaf88fce663454
repository-discovery-invalid-query.json     2137552dab764366966bf92749ced2e28b9dfc2aa56847b99882d6a71cd21b37
repository-discovery-run.json               09afe65bb371a8bc71cb5f9e816acfb006dcfb10a03126716cab1a31c9e597ef
repository-discovery-scorecard-silent.json  443c25b2b027882d973274b7b6bca30a8d2e2653c3b3771a4b6dd5da419b3cdd
sampleproject-sandbox-negative-probe.json    5bc6629c81cddd05063fe491e7ffbb25a1ba39ac397f5ccfd61f457d19aa4a34
sampleproject-sandbox-negative-review.json   3c40fee33a4d28e5153c401b552e97a2aa6cdd43d50fca588f230e473bc83637
sampleproject-sandbox-positive-probe.json    1bb68ace709d368286c55f9b28e8ce6ed33e76328775e5d82d5763a614a71392
sampleproject-sandbox-positive-review.json   fc80f1edb9b4a3ae8dbfa6b5716309c78bab2405ed4cca3b535bdfab7ea79205
swerex-1.4.0-integration-defects.txt          73b791a1d107d6e99e437c08f6efa5899908b32dbf7b2fc6cee4aca3cd5a9567
adapter-mutated-definition-defect-probe.json e9109acf3986e104993b5a9b368bafc476477260c559568d69d69af7158f9e30
adapter-paper-title-broken-probe.json        7a3191d482fb30e302a6d0765bbc14520486837e3317b9ae06610a555bb25ee0
adapter-paper-title-positive-invalid-source-probe.json 521880c3fb0bdb69cffca160f83919a1d7ff4279eb3cfdc77cf311375b7dc314
adapter-paper-title-positive-probe.json      bffffadfc35893de0bbfd1207be43df9aa0a488536397ccd91460e3dee5bb6e7
adapter-paper-title-positive-review.json     6e11e50517eb88f5484009d93e3936dcd6d192bccd430f0d25810d41a29dafe3
composition-working-vs-broken-probe.json     5d7c2c1252b81b426d94eebd78265443f650fa4304d8b311108c378b46f85b21
composition-working-vs-broken-review.json    0c39ac40f0e9953315ad51daceec1ac1b48f48bc12420fe949ea19edc06c99dc
composition-mismatched-workload-failure.json a04804393c0bd9f604efdb33b782d1e59565d6617baafd44c354fcf3af293f77
```
