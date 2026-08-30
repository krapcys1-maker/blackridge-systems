# Composition Reuse v1

This workload measures the one thing the product claims and nothing else measured: **selection**.

## Why it exists

Blackridge's rule is *reuse → inspect → verify → adapt → integrate → test → build only the gap*.
Before this benchmark, no workload observed the reuse decision:

- the retired Duplicate Finder workload measured code generation. Its `component_decisions`
  recorded only `python-standard-library`, so a run that wrote everything from scratch and a run
  that reused a real component were indistinguishable;
- the calibration and system-E2E workloads execute a composition a human already assembled, so
  they measure execution correctness, not choice.

This workload freezes a component pool and asks: given a reviewed implementation that qualifies,
does the solver use it — and when the pool does not qualify, does it fail closed with a reason a
reviewer can act on?

## Subject

The pool entries wrap the real `grounded-researcher-v1` component at evidence level **L3**, bound
to its actual manual review, typed promotion, and probe. Nothing here is a fixture stub: the
positive case executes the component process and validates its output against the declared
contract.

## Cases

| Case | Expected | What it measures |
| --- | --- | --- |
| `reuse-complete` | complete plan, zero adapters, zero generated files | reuse of a qualified component |
| `blocked-preferred-fallback` | qualified alternative selected | an explicit block outranks selection priority |
| `evidence-floor` | rejected: `evidence L3 is below required L4` | the ladder is not lowered to finish a plan |
| `license-blocked` | rejected: `license GPL-3.0-only is not allowed` | license policy gates selection |
| `hash-drift` | rejected at the launch lock *and* the review promotion | artifact identity is checked twice, independently |
| `adapter-gap` | incomplete, component still eligible | an unroutable graph is not reported as an empty pool |

`adapter-gap` is the case worth reading twice. A solver that collapsed "no route" into "no eligible
component" would send a reviewer hunting for a missing dependency when the real defect is a
contract mismatch.

## Running it

```bash
python tools/evaluate_composition_reuse.py
```

Eight tests must pass. The evaluator owns the expected outcomes; a candidate under measurement may
not read or modify it.

## Re-freezing

The cases lock the exact component artifact and manual-review SHA-256 values. If either changes,
`hash-drift` stops being the only failing case and the honest response is to re-review, not to
re-freeze silently. To regenerate deliberately:

```bash
python tools/freeze_composition_reuse.py
```

`--check` reports drift without writing, which is the form suitable for CI.

## Limitations

- One capability, one reviewed implementation. This does not measure multi-capability routing,
  adapter synthesis, or fan-in.
- It measures the deterministic solver. It says nothing about discovery quality, and nothing about
  whether a better component exists upstream.
- Passing every case is selection evidence. It is not an L4 system-verification verdict, and it is
  not a claim that an arbitrary real-world system can be composed.
