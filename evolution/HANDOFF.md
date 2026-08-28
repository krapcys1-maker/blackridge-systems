# Project handoff

Updated: 2026-08-28 13:25 +03:00.

## Read this first

The current architectural champion is **v1.1**, packaged as `0.1.1`. It is the original v1 control
plane enhanced with selected planner, operator, GitHub-search, deny-policy, repair-feedback, and
hash-gated generation boundaries. It is not v2. The name **v2** is reserved for the fresh challenger
architecture in round 001.

The earlier isolated v2 r4 prototype lost v1 verification and safety gates and was rejected. Its
artifacts remain immutable evidence under `benchmarks/blackridge-self-hosting-v2` and the sibling
`blackridge-experiments` directory. Do not promote or silently delete them.

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

## Current status and next action

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

The next action is to create a corrected B proposal with typed post-commit subscriptions,
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
- The main repository worktree is intentionally uncommitted and contains user-owned prior changes.
  Do not reset, clean, stash, or overwrite them.
- No commit or push has been performed for the current worktree.

Machine-readable truth is in `evolution/state.json`; the active manifest is
`evolution/rounds/002/manifest.json`. Round 001 is retained as terminated evidence.

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

Next action: build B+A from the independent ledger base plus v1's measured evidence controls,
component locking, and black-box test contract. Give it three identical attempts. If it beats the
provisional 1/3 or ties with better cost/interventions and no regression, it may justify switching
the architectural base. Otherwise the improved v1 line becomes the round champion and creates the
next fresh challenger.
