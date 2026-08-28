# Candidate B: ledger foundry

This is the B+A round-002 hybrid: the fresh ledger architecture built from the public task,
request, verified-component evidence, and known evaluator contract. It does not import or execute
the champion source tree. It adopts the champion line's evidence validation, black-box CLI test
contract, and exact component locking when only generated tests require repair.

Each build appends immutable evidence for the provider completion, strict compiled proposal,
materialized file hashes, generated tests, composition records, and sandbox preflight. The measured
round permits at most three automatic repairs using retained failure evidence. The independent
round evaluator remains outside this repository and reruns only after the builder stops.
