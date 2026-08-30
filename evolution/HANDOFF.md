# Project handoff

Updated: 2026-08-30.

## Read this first

**The product goal, restated by the owner and now the only one:** build systems from ready
components on GitHub. Design the architecture, identify the pieces, find their best versions,
write only what does not exist, connect it, test it. The scientific-researcher and LLM-memory
directions are a **separate project** and are out of scope here.

**The foundry has now produced its first system.**
`systems/supply-chain-auditor-v1` audits a repository's declared dependency versions and reports
its independent security posture. Two of its three capabilities are satisfied by existing public
infrastructure (OSV, OpenSSF Scorecard); the third is glue that was written because nothing
publishes it. Live run against `pallets/jinja`: verdict `findings`, 16 vulnerabilities, posture
5.9, zero contract-validation errors.

Before this, the foundry had composed only fixtures. Every entry in `components/` had been written
by hand.

**Read `../blackridge-run-001-supply-chain-auditor/INTERVENTION-LOG.md` before planning anything.**
It is a measured record of one real end-to-end run: what worked, what broke, and what it cost to
do the broken parts by hand. It replaces roadmap opinion with evidence, and it defines the build
order below.

## The rule that governs this project now

> **No new control-plane feature until a real run proves it is needed.**

This is the project's own "experiment before expansion" doctrine, finally applied to the foundry
itself. Had it been in force, rounds 002-007 would not exist: seven measured rounds improved a
duplicate-finder from 1/3 to 3/3 while the product's actual claim went untested.

## Measured build order

From the intervention log, in order. Nothing else earns control-plane code yet.

1. ~~**`adopt`**~~ — **done.** `src/blackridge/adoption.py`. A human declares the contract; the
   operator writes the adapter body; the control plane keeps schema validation, the repair budget,
   hashes, and acceptance. A proposal that fails its own tests is never written to the registry.
   Falsified against a hand-written component: `adopt` reproduced `scorecard-posture` from the
   contract alone in two iterations (152 lines, 6 tests) and matched its contract-governed
   behaviour on every case. The only divergence was the *error envelope*, which the contract never
   declared — a finding about the spec format, not the bridge.
   **Next change to `AdoptionSpec`, and nothing beyond it:** declare the rejection envelope
   alongside the success contract.
2. **Capability kind** — `found` / `written` / `platform`. The run proved the distinction is real:
   `audit-merge` should never have been searched for, and discovery correctly returned nothing
   usable for it after spending its budget.
3. **Relevance over popularity** — an irrelevant repository scored 90.4 against a relevant one at
   83.6, and `anchore/syft` never surfaced despite being in this repository's own
   `upstream-catalog.yaml`.

### Superseded ordering (kept for the record)

1. **`adopt`** — repository plus a declared contract to a registry-grade `ComponentOption`.
   This is the single blocker: `blueprint` emits a repository *name*, `ComponentOption` needs a
   runnable artifact with argv, hashes, and an evidence chain. Nothing bridges them, which is why
   every component so far was hand-written.
   Doing it by hand measured the cost: 90-160 lines of contract-shaped wrapper per component, plus
   a JSON Schema per contract. That is the size of artefact the existing generator already produces
   reliably, so `adopt` needs no new generation machinery — only a new target and a
   `ComponentOption` emitted around the result.
   **Falsifiable first test:** regenerate one of the three hand-written auditor components and
   compare against it.
2. **Capability kind** — `found` / `written` / `platform`. The run proved the distinction is real:
   `audit-merge` should never have been searched for, and discovery correctly returned nothing
   usable for it, after spending its budget.
3. **Relevance over popularity** — an irrelevant repository scored 90.4 against a relevant one at
   83.6, and `anchore/syft` never surfaced at all despite being in this repository's own
   `upstream-catalog.yaml`.

Contracts with schemas were solved for this system by hand in `tools/freeze_supply_chain_auditor.py`.
Generating them stays deferred until a second system shows what actually varies between them.

## Repository state

- 338 tests passing, 3 skipped; coverage 78%. Ruff, format, mypy clean.
- The champion-challenger loop is **closed** at round 007; champion v1.8, integrated at `b6c5521`.
- Hash locks were confirmed fail-closed on a real system, not a fixture: `ruff format` changed the
  component bytes and the next solve returned `Selected components: none` until the definition was
  re-frozen.
- Workspace cleaned from 30.8 GB / 277k files to **4.9 GB / 63k files**. Removed: 52 virtual
  environments, 316 tool caches, and the scientific-auditor model weights (a separate project;
  public checkpoints whose SHA-256 values stay recorded). Every metric, report, manual finding,
  frozen archive, and git object was kept. Manifest: `evidence/workspace-cleanup.json`.
  Explorer had been over-reporting by 8.77 GB because it counts hard links repeatedly.

## Modules to freeze rather than extend

Working, tested, and not load-bearing for the goal above. Keep the code and its tests; stop
developing them until a real run demands it.

- `benchmark.py` (1232 lines) — A/B harness with nothing to compare yet.
- `release_compliance.py` (937 lines) — release engineering for an unreleased product.
- `evolution.py` (525 lines) — the closed champion-challenger loop.

## Why the loop is closed

Four reasons, recorded in `evolution/state.json` under `loop_status`:

1. The v2 ledger architecture lost all seven rounds (0/3, 1/3, 1/3, 1/3, 1/3, 1/3, 0/3). Its
   architectural question is answered; rerunning it buys nothing.
2. The Duplicate Finder workload saturated at 3/3. A benchmark every candidate passes cannot
   discriminate between candidates.
3. That workload's `component_decisions` record only `python-standard-library`. It never observed
   reuse, so seven rounds of tuning optimized generation while the product's actual claim went
   unmeasured.
4. Continuing would have kept improving a number that no longer means anything.

Do not open round 008. If a future change needs measurement, measure it against the successor
benchmark below.

## Successor benchmark

`evolution/benchmark/composition-reuse-v1.json`, with cases in
`benchmarks/composition-reuse-v1/cases` and the evaluator at
`tools/evaluate_composition_reuse.py` (8 tests, all passing).

It measures **selection**, which nothing measured before: given a pool containing a reviewed L3
implementation, does the solver reuse it, and does it fail closed with an actionable reason when
the pool does not qualify? Six frozen cases cover reuse, blocked-entry fallback, the evidence
floor, license policy, artifact hash drift, and an unroutable contract graph. The positive case
executes the real component and validates its output against the declared contract.

The cases lock the exact component artifact and manual-review SHA-256. Re-freezing with
`tools/freeze_composition_reuse.py` is a deliberate, separately reviewed act — `--check` reports
drift without writing.

## Current repository state

- 314 tests passing, 3 skipped (was 260); coverage 77% (was 74%).
- `cli.py` coverage 36% → 48%. Nineteen commands previously had no test at all; they now have
  registration, help-render, fail-closed, and — where hermetic — full command-body tests in
  `tests/test_cli_command_surface.py` and `tests/test_cli_command_bodies.py`.
- Ruff, format, and mypy all clean.
- The README no longer leads with a twenty-five item claim list. It states what works, what is
  not built, and what is deliberately human, and it links measurement to evidence.

## Known outstanding work

- **Rotate the GitHub PAT and DeepSeek key in `.env`.** The file is correctly gitignored but the
  PAT was exposed in a terminal session, and `.env` contains a malformed bare-token line.
- `tools/archive_experiments.py` identifies 8.77 GB of byte-identical duplicated model weights in
  the sibling `blackridge-experiments` tree, reclaimable by hard link with no evidence loss, plus
  0.29 GB of regenerable `__pycache__`. It has not been applied; it is a dry run by default.
- `provenance.py` (56%) and `quality.py` (55%) remain the weakest tested modules.
- Adapter synthesis beyond JSON Patch, multi-capability composition at scale, and production
  sandbox execution are unbuilt and unmeasured.

## What follows is historical

Everything below is the retained round-by-round record. It is evidence, not instruction. Where it
conflicts with this header or with `evolution/state.json`, the header and the state file win.

## Completed bootstrap work

- Fixed DeepSeek call, token, prompt, response, and conservative cost bounds.
- Added strict natural-language planning with 4–10 capabilities and bounded atomic searches.
- Added official GitHub CLI discovery, correct `license.key` handling, exact query records,
  case-insensitive deny policy, and explicit partial-budget retention.
- Added strict generated-proposal schemas, portable path rejection, supplied-evidence enforcement,
  review-feedback hashes, schema-rejection evidence, and exact-SHA materialization.
- Repeated the duplicate-finder experiment through ten provider attempts. Unsafe output collision,
  hard-link, symlink, recursion, schema, and missing-test variants were rejected before promotion.
- The accepted representative artifact passed 9 builder tests plus 7 independent adversarial tests
  in the pinned non-root networkless sandbox.
- The repository passed 222 tests, coverage above 71%, lint, format, mypy, compile, Bandit,
  dependency audit, notices, provenance, wheel/sdist checks, wheel smoke, and system E2E controls.
- Added deterministic champion selection in `src/blackridge/evolution.py`. A round requires the
  champion, A, B, A+B, and B+A; all bind to one benchmark revision and an independent evaluator.
- Added isolated challenger architecture proposals, hash-bound review feedback, JSON-safe
  rejection retention, flow-interface validation, and deterministic interface-only repair with
  exact parent-completion provenance. Mechanical repair is never promotion evidence.

## Frozen identities

- Champion snapshot: `D:\Skladacz aplikacji\blackridge-champion-v1.1-snapshot-20260828-r1`
- Champion snapshot commit: `0bdfd5ac055118fcd38f157522a88f460d6ba64e`
- Champion source archive SHA-256:
  `196551749cb767b75e31302f55a7e17e10178bfe86994ab2a295330ce10bb58e`
- Public benchmark SHA-256:
  `77495687a7fb65a3fc4e90691093443924a20292a9f42a9f9ce2704d84a827f9`
- Evaluator-only holdout:
  `D:\Skladacz aplikacji\blackridge-evolution-evaluator\round-001-holdout.json`
- Holdout SHA-256:
  `04cd24be9e9ae39327f146bb76ed299b4fd42be42a0c3f1615a18716a178b86e`
- Candidate A snapshot: `D:\Skladacz aplikacji\blackridge-evolution-round-001\candidate-a-v1.2`
- Candidate A snapshot commit: `ee72dd6b39f3e98ab4384cb708e7df98a81a6111`
- Candidate A GitHub branch: `evolution/candidate-a-v1.2-round-001`
- Candidate A source archive:
  `D:\Skladacz aplikacji\blackridge-evolution-evaluator\candidate-a-v1.2-round-001.tar`
- Candidate A source archive SHA-256:
  `530ca9477984e858e2a198d35e2d90f08df60ddadc51f918be12b6405cd2dcc5`

## Historical round-001 status

Round 000 is a completed bootstrap that selected v1.1. Round 001 is active, but no challenger has
won and no five-way benchmark has run.

`candidate-a-v1-2` is frozen from the clean champion snapshot. It adds evolution controls only,
is packaged as `0.1.2`, and passed 229 tests with 3 skips, 72.15% coverage, Ruff, format, mypy,
compile, Bandit medium/high, build, and Twine checks. These are candidate-owned/static gates, not
promotion evidence.

Fresh architecture B was attempted ten times using only the public brief and public benchmark;
champion source was never provided. Nine provider completions were retained. Attempt 003 exposed
and then caused repair of a rejection-serialization bug; it is classified as infrastructure
failure, not candidate failure. Known provider cost is USD 0.03821941 plus the unknown cost of the
unretained attempt 003. Attempts 2, 7, 8, 9, and 10 reached manual architecture review but failed
critical pre-build gates. Attempt 010 is structurally valid after recorded interface-only repair,
but remains rejected because it has untyped broadcast routing, possible evidence recursion, no
independent verification of generated adapters, a non-executable first slice, and a control plane
that does not actually gate dispatcher releases. Do not implement or hybridize it.

The historical next action was to create a corrected B proposal with typed post-commit subscriptions,
idempotent evidence collection, exact-hash adapter verification, control-authorized releases, and
an executable minimal slice. Continue to use public inputs only. A+B and B+A remain blocked until B
has measured strengths rather than model claims.

After both are measured, transfer evidence-backed strengths in both directions to create `A+B` and
`B+A`. Do not construct hybrids from model claims alone. Then run all five snapshots through the
same evaluator and call `blackridge select-champion` on the retained round evaluation JSON.

## Safety and workflow rules

- Never let a builder read the evaluator-only holdout or choose the winner.
- Never edit a candidate during measurement; create a new candidate identity after any change.
- Reject critical regressions before weighted comparison.
- Preserve failed completions and experiments. Never rewrite a failed attempt as a pass.
- A schema-valid or mechanically repaired architecture is not build-approved; semantic review is
  a mandatory pre-build critical gate.
- Keep secrets out of artifacts and prompts except at the narrow provider boundary.
- Preserve unrelated user changes. Do not reset, clean, stash, or overwrite them.
- Commit and push completed round evidence before beginning another candidate measurement.

Machine-readable truth is in `evolution/state.json`; the latest completed manifest is
`evolution/rounds/004/manifest.json`. Round 001 is retained as terminated evidence.

## Measurement correction and round 002

Round 001 was terminated without promotion. A byte-identity audit proved that candidate A did not
change planning, operator, discovery, generation, composition, workflow, the Duplicate Finder
task, or its evaluator. Tournament-control code is not a better generated project. B failed its
pre-build architecture gate. The retained champion is therefore v1.1.

Round 002 is frozen before builders start. It uses only the previously measured Duplicate Finder
task, the exact independent seven-test evaluator, the retained nine-test expectation, the same
normalized discovery/request/verified-component inputs, and the complete existing v1 CI/system-E2E
regression gates. Every candidate gets three attempts. Exact ties retain v1.1. Do not add new
projects or alter expectations inside this round.

## Round 002 progress update — 2026-08-28 18:15 +03:00

This section supersedes the earlier "builders not started" status above. The selected champion is
still v1.1 until B+A is measured, but the provisional leader is now an improved v1 line, not v2.

- Exact champion v1.1 measured 0/3 on fresh one-shot builds.
- Candidate A with strict acceptance coverage measured 0/3.
- Fresh ledger B measured 0/3. Its run also breached the protocol because its builder received the
  external evaluator path instead of only the repository copy; it is ineligible for promotion.
- The first A+B hybrid and the component-lock successor each measured 0/3.
- The black-box-test successor at commit `5a0c6e348c725578650fbd668245a39658083e92`
  measured 1/3. Its passing attempt had 11/11 generated tests, public 7/7, independent 7/7,
  zero manual interventions, three repairs, and provider cost USD 0.03003612.
- The successful program SHA-256 is
  `dc74d49358fca82ad1b0eb5b33689a0f70e5b2697fd94066835ec017d4404142`.
- Full clean release gates passed, including dependency consistency, Ruff, format, mypy,
  compileall, Bandit medium/high, pip-audit, notices, provenance, coverage, build, Twine,
  installed-wheel optional dependency resolution, and Docker system E2E/fail-closed controls.
- The four measured v1-line improvements were cherry-picked into the main experimental branch.
  This is integration for continued development, not final round selection.

Machine-readable measurements and evidence hashes are in
`evolution/rounds/002/measured-results.json`. Raw provider, proposal, test, evaluator, and release
evidence remains under `D:\Skladacz aplikacji\blackridge-evolution-round-002`.

## Round 002 final result — 2026-08-28 19:10 +03:00

Round 002 is complete. B+A was built from the fresh ledger base plus v1's evidence validation,
component locking, and black-box CLI test contract at commit
`6bd8882b536164503c1a5a2d04952a6aa6408e2e`. It measured 1/3. Its successful attempt passed 10/10
generated tests, public 7/7, and independent 7/7 after two repairs for USD 0.01926666 with zero
manual interventions.

B+A did not replace the improved v1 line despite the lower successful-attempt cost. One attempt
crashed the builder when the provider returned malformed JSON, engine test coverage was only
46.02% against the frozen 70% gate, and the standalone foundry lacked package, notice/provenance,
wheel/sdist, and installed-wheel system-E2E gates. The winner rule requires equal-success candidates
to have no worse safety or regression result before cost can break the tie.

The selected champion is therefore the enhanced v1 line, now named **v1.2**, represented by
`candidate-blackbox-tests-round-002` at commit
`5a0c6e348c725578650fbd668245a39658083e92`. Do not call it v2. The B/B+A architecture remains an
isolated v2 challenger line, not the product base.

Evidence worth transferring into the next v1 iteration is narrow and explicit: test whether giving
the safe v1 builder the known public evaluator contract reproduces B+A's lower repair cost, preserve
append-only iteration ledgers, and add a regression test for malformed provider JSON. Do not import
B+A's unpackageable foundry base or its weaker gates. Round 003 should compare that enhanced v1
candidate against a hardened v2.2 challenger on a newly frozen benchmark without changing tests
during measurement.

## Round 003 progress — 2026-08-28 19:25 +03:00

Round 003 reuses the exact frozen Duplicate Finder benchmark at the user's request. Candidate v1.3
added the useful B+A idea: the repository's known public evaluator contract is now supplied to the
safe v1 generator and hash-bound in generation and rejection records. A premeasurement run exposed
a real rejection-evidence serialization bug when Pydantic included a `ValueError` in validator
context. That crash is preserved; it was not misreported as a complete three-attempt result.

The JSON-safe successor v1.3.1 at `8a37ae109c9c3dda8d86194a6c96cb1242daa655` measured 2/3 versus
the retained v1.2 champion's 1/3. Both successes required one repair, zero manual interventions,
and passed generated tests plus independent 7/7 evaluation. One failed attempt also proved that a
truncated provider JSON response is retained as a bounded `ExternalToolError` and no longer crashes
the series.

All clean release gates passed again: dependency consistency, Ruff, format, mypy, compileall,
Bandit, pip-audit, notices, provenance, 238 tests with 3 skips, 72.47% coverage, wheel/sdist, Twine,
installed wheel extras, optional verifier resolution, and Docker system-E2E/fail-closed controls.
This makes v1.3.1 the provisional leader, not yet the final champion. Round 003 still requires the
hardened v2.2 candidate and both transfer hybrids before selection.

## Round 003 final result — 2026-08-28 23:11 +03:00

Round 003 is complete. Every required slot used the unchanged Duplicate Finder benchmark, the same
three attempts, the same public and independent evaluator hashes, the same repair limit, and zero
manual interventions:

- retained v1.2: 1/3;
- initial v1.3: rejected before measurement because rejection evidence was not JSON serializable;
- JSON-safe v1.3.1: 2/3, total provider cost USD 0.04173154;
- packaged ledger v2.2: 1/3, total provider cost USD 0.05314388;
- A+B compact v1 profile: 2/3, total provider cost USD 0.06229176;
- B+A acceptance-mapped ledger: 1/3, total provider cost USD 0.08316530.

The selected champion is **v1.3.1** at
`8a37ae109c9c3dda8d86194a6c96cb1242daa655`. It improved success over v1.2 and passed all critical
repository, dependency, provenance, package, installed-wheel, and Docker system-E2E gates. A+B tied
its 2/3 success rate and zero interventions, but cost more over the frozen series, so the incumbent
v1.3.1 wins under the predeclared tie-break rule. This remains an enhanced v1, not v2.

Useful evidence from the losing lines is retained rather than blindly merged. The compact prompt
can produce a cheap first-call success but did not lower total series cost, so it stays optional.
The ledger line's exact acceptance mapping and component locking are sound safeguards, but v1.3.1
already has richer equivalents. In failed v2/B+A attempts the generated program could pass 7/7
while model-written tests remained broken. Round 004 should therefore keep v1.3.1 as champion and
build v2.4 around specialized test-only repair, then create A+B and B+A and rerun the same benchmark.

Raw evidence is under `D:\Skladacz aplikacji\blackridge-evolution-round-003`; machine-readable
selection data is in `evolution/rounds/003/measured-results.json`.

## Round 004 progress — 2026-08-28 23:31 +03:00

The final round-003 champion remains v1.3.1 until all round-004 slots are measured. Candidate A is
an enhanced v1 descendant named **v1.4**, never v2. At commit
`871a469963420d9965c8f477f7c9a31a5b2c301d` it adds a narrow test-only repair contract: after a
product passes the public evaluator, every non-test file, run command, component decision, and
prior proposal hash is locked; the operator may return only replacement black-box tests. Invalid
repair output is rejected fail-closed and retained with JSON-safe evidence.

Candidate A measured 3/3 on the unchanged Duplicate Finder benchmark, versus v1.3.1's retained
2/3. Its attempts passed 10/10, 10/10, and 11/11 generated tests plus independent 7/7 evaluation.
Total provider cost was USD 0.03091980, with one repair across the series and zero manual
interventions. Full isolated gates passed: dependency consistency, Ruff, format, mypy, compileall,
Bandit, pip-audit, notices, provenance, 240 tests with 3 skips, 72.76% coverage, wheel/sdist, Twine,
installed optional dependencies, and fresh-clone wheel system E2E/fail-closed controls.

Do not overstate causality: none of the three attempts entered test-only repair. Attempt 1 needed a
full repair because both its generated tests and program failed; attempts 2 and 3 passed on the
first call. The snapshot is the provisional measured leader, but this series alone does not prove
that the new path caused the improvement. v2.4 and both hybrids remain mandatory before selection.

Raw evidence and the reusable round harness are under
`D:\Skladacz aplikacji\blackridge-evolution-round-004`. The candidate summary SHA-256 is
`0b0b50be2824d2e0343a3171c1ef2d47bd9fff9a9408c024cabaf8222e84ffc5`.

## Round 004 final result

Round 004 is complete. v1.4 won with 3/3. A+B and B+A each reached 2/3, v2.4 reached
1/3, and retained v1.3.1 had 2/3. The selected base remains the enhanced v1 line; do not call it
v2. The exact v1.4 candidate commit is `871a469963420d9965c8f477f7c9a31a5b2c301d` and its code was
integrated into the main experimental branch at `299cb2f`.

The test-only boundary was exercised by A+B: it kept the passing program byte-exact, but DeepSeek
returned the same failing 12-test proposal three times. This proves the lock works but the repair
prompt needs diversity or explicit delta feedback. Start round 005 from v1.4; keep the ledger line
as challenger and do not switch architectural bases until it exceeds the champion on the frozen
critical gates and success rate.
