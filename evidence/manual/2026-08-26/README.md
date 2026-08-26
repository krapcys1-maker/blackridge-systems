# Manual verification — 2026-08-26

Environment: Windows, Python 3.12.10, Blackridge editable install. External observations are a
time-stamped snapshot and may change upstream.

## Segment verdicts

| Segment and scenario | Verdict | Manual observation |
| --- | --- | --- |
| capability contract schema | PASS | Six self-hosting capabilities and seven acceptance scenarios loaded. A deliberately duplicated scenario ID was rejected. |
| deps.dev / `paper-qa-package-evidence` | PASS with blocker outside this scenario | `paper-qa@2026.8.12` resolved to 75 nodes and 16 direct dependencies. deps.dev reported `non-standard`; GitHub reported `Apache-2.0`, so package approval remains blocked pending license reconciliation. |
| deps.dev negative package and version inputs | PASS | A nonexistent package and nonexistent requested version both exited 2 while retaining explicit failure evidence without a verdict. |
| exact-version guard / `exact-version-not-substituted` | PASS | The failed GROBID 0.9.1 lookup retained the exact input and error and did not substitute another package version. |
| deps.dev Maven coverage for GROBID 0.9.1 | FAIL as a source of the requested release | Exact 0.9.1 was unavailable. An unpinned query selected the only indexed version, 0.3.4 from 2015, while GitHub reports release 0.9.1 from 2026-08-04. |
| repository discovery / `known-sandbox-query` | PASS for discovery, not selection | The corrected query returned SWE-ReX and other real sandbox implementations. The top metadata score belonged to a broader agent framework, proving that L0 ranking is only an inspection queue. |
| original discovery contract | FAIL | The original YAML scenario described sandbox discovery but issued the unrelated query `code search agent`. The failed artifact was retained and the query corrected before rerunning. |
| OpenSSF Scorecard observation | PASS after defect correction | The first run silently stored seven missing scores. The corrected run retains seven explicit `not-found` warnings. A positive control against `ossf/scorecard` returned status `available` and score 9.0. |
| manual review gate negative cases | PASS | Missing `--observed` and an unknown scenario both exited 2 and wrote no review file. |
| provisional blueprint gate | PASS | The artifact stays `release_ready: false`, evidence L0, requires L2, and warns that the leading candidate still needs inspection and sandbox boot. |
| environment construction and sandbox boot | NOT RUN | Adapter not implemented yet. |
| deterministic component adaptation | NOT RUN | Adapter execution and before/after contract fixture not implemented yet. |
| composed-system end-to-end verification | NOT RUN | Requires the two preceding segments. |

## Important findings

1. Package and repository license metadata can disagree. Both facts must remain visible.
2. Registry coverage can be stale even for an actively released upstream project.
3. Search relevance plus popularity can rank a broad framework above a more precise component.
4. A missing Scorecard is not a score of zero and not an API success; it needs an explicit state.
5. An exit code proves only that a command completed, not that the produced artifact is correct.

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
```
