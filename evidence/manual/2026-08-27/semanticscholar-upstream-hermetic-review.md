# Semantic Scholar 0.12.0 — hermetic upstream test review

## Result

The exact release commit `3189ecb80bd985b6cd9b4a56fb410b05515f0f15`
passes all 130 upstream tests after applying the named test-harness-only patch.
Production package sources were not changed. Branch coverage of the package was
91.40% (1,177 of 1,240 statements and 269 of 342 branches covered).

## Weaknesses found manually

1. The upstream VCR configuration supplies `record_mode=['new_episodes']` as a
   list. VCRPy 8.3.0 rejects it during test discovery because the value must be
   a string.
2. The synchronous and asynchronous timeout tests have no VCR cassette and
   therefore depend on a live Semantic Scholar request. They are not
   deterministic and cannot pass in a genuinely networkless runner.

The repository patch changes the record mode to `none` and injects a local
`httpx.TimeoutException` for those two cases. This makes missing fixtures fail
closed and removes the hidden network dependency.

## Isolation and dependency controls

The successful run used the exact hashed lock, 13 pre-downloaded Linux wheels,
`pip --no-index --require-hashes --only-binary`, a read-only source mount,
Docker `--network none`, a read-only root filesystem, all capabilities dropped,
`no-new-privileges`, and explicit CPU, memory, swap, and PID limits. No test
container remained afterward.

## Decision

This materially strengthens the L2 adapter evidence, but does not justify L3.
The remaining blockers are upstream release provenance/Scorecard coverage and a
wider frozen adapter-contract benchmark that tests field elision, malformed
nested objects, pagination termination, and retry boundaries at the adapter
surface.
