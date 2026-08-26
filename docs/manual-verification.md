# Manual verification policy

Blackridge does not treat a successful command, green CI run, metadata score, or LLM judgment as
proof that a component performs a capability.

The verification loop is:

1. define an observable scenario with concrete input and expected behavior;
2. run a real probe against a pinned or otherwise identified subject;
3. retain raw inputs, outputs, warnings, source URLs, and failures;
4. inspect the artifact and independently cross-check important claims;
5. record a named `pass`, `fail`, or `blocked` verdict with observations and caveats;
6. keep failed and superseded evidence instead of rewriting history.

`ProbeEvidence` deliberately has no verdict field. `ManualReview` cannot be created without a
reviewer, expected behavior, concrete observations, explanatory notes, and the SHA-256 digest of
the exact probe file that was inspected.

## Status vocabulary

- **PASS** — the retained evidence satisfies this exact scenario, not the whole component.
- **FAIL** — the evidence contradicts at least one required observation.
- **BLOCKED** — the evidence is insufficient or contradictory.
- **NOT RUN** — the segment has not been implemented or exercised; it is never shown as green.

The first retained run is documented in
[`evidence/manual/2026-08-26/README.md`](../evidence/manual/2026-08-26/README.md).

For environment probes, manual inspection must include the requested and observed commit, resolved
image ID, runtime identity and limits, every command's argv/exit/stdout/stderr, produced artifact,
before/after host hashes, commands skipped after a failure, and container cleanup. Docker isolation
without a host mount is adequate for the current disposable boot experiment, but not evidence of a
hardened adversarial or multi-tenant security boundary.

For adapter and composition probes, inspect the unmodified declarative operations, before/after
schema errors, complete output objects, source-preservation differences, and mutation flags. A
patch that applies without raising is not a passing contract result. The paired negative must use
the exact same input and target schema and differ by the explicitly retained operation delta.

For supply-chain probes, inspect the requested and observed commit, repository LICENSE blob,
direct-dependency license results, SBOM counts and hashes, unknown license coverage, scanner image
digests and argv, Scorecard status, OSV exit/findings/scope, every distribution provenance response,
source-tree cleanliness, and container cleanup. A valid Git commit signature is not package
provenance. A scenario may pass because missing and adverse evidence was represented correctly
while the inspected release itself remains blocked.
