# Scientific researcher replicated A/B — manual review

Date: 2026-08-27  
Reviewer: Codex `/root`, primary workspace audit agent (not an independent human reviewer)  
Verdict: **FAIL**  
Automatic winner: **none**

## Claim tested

With the same model, settings, runtime limits, public contract, and hidden evaluator, the current
Blackridge reuse-first material should improve task success or reach the same success with less
new code, time, cost, or repair effort than a from-scratch builder.

The experiment used three fresh one-shot attempts per method. Builders received no evaluator case,
expectation, prior output, or other arm's log. The Blackridge arm additionally received the current
component blueprint, upstream catalog, and research-landscape evidence. This treatment contained no
component meeting the required evidence gate; all Blackridge builders correctly reported zero
reused source lines rather than importing blocked or provisional code.

## Final raw results

| Attempt | Method | Task success | Critical checks | Builder seconds | Generated lines | Reused lines | Total model tokens |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | from-scratch | false | 16/22 | 10.284 | 95 | 0 | 2,992 |
| 1 | blackridge-hybrid | false | 16/22 | 15.532 | 198 | 0 | 8,939 |
| 2 | from-scratch | false | 16/22 | 10.519 | 100 | 0 | 3,173 |
| 2 | blackridge-hybrid | false | 16/22 | 14.766 | 168 | 0 | 9,153 |
| 3 | from-scratch | false | 19/22 | 14.879 | 145 | 0 | 3,664 |
| 3 | blackridge-hybrid | false | builder gate failed | unavailable | 239 proposed | 0 | 10,137 |

Task Success Rate is `0/3` for both methods. Model cost in USD is unavailable from the provider and
was not inferred. There were no evaluator-guided repairs. Successful deliverables were committed
before evaluation and remained clean afterward.

The first two attempts of both methods abstained correctly on the insufficient-corpus case but also
abstained on the answerable case. Their lexical relevance thresholds could not identify ten
supporting documents. The third from-scratch attempt answered and grounded every quote, but filled
the ten-source quota with four gardening distractors. It failed required evidence coverage,
irrelevant-source exclusion, and required-concept coverage. The third Blackridge bundle contained
239 lines against the frozen 220-line gate; it also contained an unused `make_claim` function and a
large duplicated stop-word table, so it was rejected before clean installation or evaluation.

## Manual artifact inspection

- Every evaluated process exited zero, produced one stdout JSON artifact, wrote no stderr, and did
  not time out. Exit zero did not override failed artifact checks.
- All five evaluated sources were read completely. They use only the Python standard library and
  contain no network calls, subprocesses, shell execution, dynamic evaluation, or file access.
- Independent Ruff inspection found 5, 15, 2, 83, and 4 findings in the five committed candidates.
  These were not silently repaired. The largest concentration was duplicated stop-word entries in
  Blackridge attempt 2.
- Candidate workspaces contain no symlinks. All five evaluated repositories are clean at the exact
  commits retained in the manifest.
- Builder prompts, raw API envelopes, bundles, and candidate workspaces were scanned for private
  case IDs, request IDs, document IDs, a unique hidden quote, and evaluator-case paths. Every scan
  returned zero hits.
- The functional and robustness case bytes, public contract bytes, evaluator bytes, model controls,
  Docker image, network policy, resource limits, and telemetry source matched within each evaluated
  pair. No benchmark container remained afterward.

## Harness defects found manually

Two orchestration pilots were excluded before the experiment: the first did not retain an API
envelope before JSON parsing; the second retained a response truncated at exactly 8,192 tokens.
Thinking mode was then explicitly disabled and a 220-line/18,000-character candidate budget was
frozen for both methods.

The initial evaluator revision `1.0` also ran Docker without `--interactive`, so every candidate
received empty stdin. Those observations are invalid and were not used. Revision `1.1` adds the
stdin attachment, was recalibrated against the reference and green-exit broken controls, and was
used to reevaluate the already immutable candidate commits. The recalibration retained identical
controls, accepted the reference, rejected the broken artifacts despite zero exits, and detected
11 concrete broken checks.

Windows additionally exposed an `OSError(22)` race while the writer thread closed a Docker stdin
pipe after process exit. The close is now bounded by `suppress(OSError)` and has a direct regression
test. This exception did not alter candidate stdout but previously polluted manual logs.

## Evidence bindings

- Benchmark v1.1 calibration: `9540fd339745df100f932b1d8e50308f46a020d3a0c0da150a5f3d57fc722927`
- Attempt 1 comparison: `68fe8dd53d7863612e1c92c0a93cd7188c3090e4a6edd18f9c21703e9244f754`
- Attempt 2 comparison: `a5e0b9eb4e4b3e92142214a4ff0ce7cac91a83772c3c76ad85b4b45aa5fea64b`
- Attempt 3 baseline evaluation: `38345e8b1c7d12ad78fd7e2f8bc67eeb0a1b23fd048d28a92085e0cf3b613fd4`
- Attempt 3 rejected Blackridge bundle: `d212ce1dd311f5b1de0f4f3e113e30502c82e54c5b3e6e05b174b5c9ba293393`
- Benchmark definition v1.1: `c6158408a4af555fac9d64d4ddb641e06ac9bf1f7999615a18e695f3b8e53262`

Raw artifacts remain under
`D:\Skladacz aplikacji\blackridge-experiments\scientific-researcher-v1-replication-20260827-v3`.
The two excluded pilot directories are retained beside it.

## Decision and next gate

Do not claim a Blackridge advantage and do not spend more attempts on the same empty treatment.
The next experiment must first qualify at least one applicable, offline scientific-synthesis or
relevance component to L2 using non-benchmark fixtures and a named review. Only then should a new
three-attempt comparison be frozen. The treatment should carry source bytes or a stable callable
component boundary, not several thousand tokens of catalog prose.

