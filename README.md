# Candidate B: ledger foundry

This is a fresh round-002 architecture built from the public task, request, and known evaluator
contract. It does not import or execute the champion source tree.

Each build appends immutable evidence for the provider completion, strict compiled proposal,
materialized file hashes, generated tests, and sandbox preflight. It may perform at most two
automatic repair calls using retained failure evidence. The round evaluator reruns independently
after the builder stops.
