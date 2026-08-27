# Scientific Claim Auditor v1

Build a command that reads one `scientific-claim-request/v1` JSON object from standard input and
writes exactly one `scientific-claim-audit/v1` JSON object to standard output. Diagnostics belong
on standard error. The system must be assembled through the same generic Blackridge composition
and generated-bundle path used for unrelated systems; domain-specific shortcuts in the control
plane are forbidden.

The first calibration corpus is the exact hash-locked SciFact archive declared in
`../upstream.json`. The fixed twelve-case slice is selected mechanically by ascending claim ID:
the first four single-label SUPPORT claims, the first four single-label CONTRADICT claims, and the
first four claims with no evidence. Case selection must not depend on candidate behavior.

For each evidence-bearing result:

- every document identity and title must exist in the frozen corpus;
- every rationale index must address an existing abstract sentence;
- every quote must exactly equal that sentence;
- the per-document verdict must match the corpus-relative evidence label;
- duplicate documents, invented sources, and uncited conclusions are forbidden.

An `insufficient-evidence` result must contain no evidence documents. Its `sources` retain the
material actually considered, so a negative conclusion remains auditable. Process exit zero is not
success. Schema validity, identity, rationale grounding, label behavior, resource bounds,
provenance, and cleanup are inspected independently.

The SciFact label is not a declaration of universal scientific truth. It states whether the
frozen corpus contains an annotated abstract that supports or contradicts the claim. A complete
system must preserve this limitation in its human-readable report.

The calibration slice is followed by the full public dev corpus, adverse controls, and separately
retained live retrieval trials. The live trial may be blocked by upstream throttling and must never
be silently substituted with replay data.
