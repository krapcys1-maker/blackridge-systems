# Blackridge A/B benchmark protocol

## What the first experiment may claim

The first experiment is a smoke test of the method, not proof that Blackridge is generally better.
It asks whether two builders can satisfy one frozen task and whether the reuse-first builder reaches
the result with different raw cost, time, repair, and generated-code measurements.

The benchmark is frozen and calibrated before either builder starts. Its evaluator must accept a
known-good protocol fixture and reject a deliberately broken fixture whose process still exits zero.
The fixtures are harness controls, not entries in the A/B comparison.

## Frozen controls

Both real runs must use the same:

- public task specification plus input and output contract bytes;
- benchmark definition and exact evaluator-module bytes;
- model identifier, model settings, and available tools except Blackridge itself;
- builder wall-time and token/cost budgets;
- base runtime, network policy, secrets, CPU, memory, and filesystem limits;
- starting repository state and deliverable contract;
- independent evaluator cases.

Only the method changes:

- `from-scratch` — no Blackridge catalog, retained component evidence, or reuse workflow;
- `blackridge-hybrid` — reuse proven components, write only missing seams;
- `reuse-only` is reserved for the later three-arm experiment.

## Isolation procedure

1. Tag the frozen benchmark commit before creating a builder workspace.
2. Export only `benchmarks/scientific-researcher-v1/public/` to each builder. Do not mount the
   evaluator tree, cases, calibration fixtures, prior candidate output, or the other method's logs.
3. Create a fresh repository and fresh conversation/task for every attempt.
4. Let an external orchestrator record start/end time, model usage, cost, repair-loop boundary,
   clean-install result, and exact final commit. Builder-reported values remain labelled as such.
5. After the builder stops, run the frozen evaluator in a separate workspace against the immutable
   deliverable. The builder may not change its output after seeing case results.
6. Retain stdout, stderr, exit code, duration, parsed output, and every artifact check.
7. A named reviewer inspects the raw evidence before assigning the attempt verdict.

The evaluator cases live in this repository for the initial smoke calibration. That is procedural
isolation, not a security boundary. Before publishing comparative claims, move evaluator cases to a
separately permissioned repository or CI environment that builder credentials cannot read.

## Raw metrics

The primary metric is task success: one attempt succeeds only when every critical check matches.
Across repeated attempts:

```text
Task Success Rate = successful attempts / total attempts
```

Always publish these raw fields beside it:

- critical and total check matches;
- functional and robustness case results;
- clean installation and reproducible rebuild;
- builder wall time and evaluator wall time;
- model usage and cost with measurement source;
- repair iterations using the same externally observed definition;
- generated and reused source lines with the counting method;
- unsupported-evidence abstention behavior;
- later: requirement-change success and change effort.

Blackridge deliberately emits no weighted success score. A presentation may calculate a secondary
score after the experiment, but it must never replace the raw table or determine the retained
manual verdict.

## Experiment sequence

1. **Calibration now:** deterministic reference versus green-exit broken fixture.
2. **Smoke A/B:** one fresh `from-scratch` attempt and one fresh `blackridge-hybrid` attempt.
3. **Replication:** three attempts per method after fixing orchestration defects without changing
   task expectations.
4. **Change phase:** apply one frozen V2 requirement to immutable successful V1 outputs.
5. **Broader study:** five tasks × two methods × three attempts; add `reuse-only` only after the
   two-arm protocol is stable.

Do not select or edit hidden cases after inspecting either method's system. A changed evaluator is a
new benchmark version and invalidates direct comparison with prior runs.
