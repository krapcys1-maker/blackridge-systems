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

## Experiment-first implementation rule

Manual verification controls the order of implementation, not only the final verdict. Before a
new segment grows beyond a thin vertical slice:

1. write the capability claim, representative real input, expected observation, and explicit
   failure condition;
2. freeze a baseline plus a deliberately broken or adverse control;
3. implement only enough of the real integration to exercise the risky assumption;
4. run it against an exact upstream revision or artifact in the intended environment;
5. inspect the complete artifacts and record `PASS`, `FAIL`, or `BLOCKED`;
6. expand or generalize only after `PASS`; otherwise retain the failure and change or stop the
   approach.

Mocks and automated tests may protect deterministic behavior after the experiment. They cannot
replace the real run. A generic framework must not be justified solely by expected future use; the
same mechanism must first work with at least two meaningfully different real subjects.

`ProbeEvidence` deliberately has no verdict field. `ManualReview` cannot be created without a
reviewer, expected behavior, concrete observations, explanatory notes, and the SHA-256 digest of
the exact probe file that was inspected.

Promoting composition evidence above L0 additionally requires a typed `EvidencePromotion`. The
promotion binds the claimed level to a successfully completed probe, provider, subject, immutable
revision, SPDX license, and exact component artifact or adapter-operation digest. Production
qualification compares every binding with the subject being selected and fails closed on a missing
or mismatched value. `review-probe` records this block only when all of
`--promotion-level`, `--subject-type`, `--subject-revision`, `--subject-license-spdx`, and
`--artifact-sha256` are supplied together.

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
source-tree cleanliness before and after scanners, cryptographic attestation verification, and
container cleanup. Attestation availability is not verification, and repository identity in a
valid PyPI attestation is not proof of the exact requested source commit. A valid Git commit
signature is not package provenance. A scenario may pass because missing and adverse evidence was
represented correctly while the inspected release itself remains blocked.

For benchmark probes, compare the evaluator-module, definition, public-specification, input-contract,
output-contract, model, budget, timeout, environment, and measurement-source controls. Inspect every
candidate exit code and complete stdout artifact, independently cross-check source identities and
quotes against the case corpus, and confirm that a green broken control fails concrete boundaries.
A harness-calibration pass is not an A/B method result. A real comparison must retain raw arms,
leave weighted score and automatic winner empty, and refuse mismatched controls before execution.

For composer probes, inspect every qualification reason, selected and rejected component, contract
transition, adapter-operation hash, command-artifact hash, generated file, provenance digest, and
Draft 2020-12 boundary result. Recompute the generated artifact hashes independently. A complete
route is only generation readiness; `release_ready` stays false until representative L4 workloads
receive named review. Missing edges must stop generation. A component that exits zero with an
invalid artifact must fail at its output contract. The v1 host runner is calibration-only;
production execution remains behind the sandbox boundary. For a saved bundle, retain the
generator's provenance SHA-256 outside the bundle and prove that a bundle with internally rewritten
hashes is rejected against the original digest.

For sandbox runs, compare the declared `sandbox_resources` in the frozen definition with
`runtime.yaml`, the Docker arguments, and the observed cgroup values. Memory swap must remain zero,
and the process must run as the declared non-root user with no effective capabilities. A larger
limit is not evidence of success; it only makes the resource boundary honest for workloads that
cannot execute under the default 1024 MiB ceiling.

If the definition declares `sandbox_image`, verify both the immutable reference and expected image
ID against the resolved Docker metadata. Supplying another image must fail before container start.

For each large model or dataset, also compare its declared `copy_timeout_seconds` with the value
recorded in `runtime.yaml` and the retained `docker cp` observation. A transfer timeout is a failed
sandbox preparation, not a model-quality failure.
