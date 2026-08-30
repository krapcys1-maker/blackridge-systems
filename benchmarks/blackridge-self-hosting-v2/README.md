# Bootstrap comparison: v1 to champion v1.1

This benchmark preserves the public task and the immutable hashes of the rejected isolated-v2 r4
prototype. The active one-off prototype runner was removed after its useful boundaries were folded
into the maintained v1 control plane.

The current experiment uses only product commands for planning, GitHub discovery, gap proposal,
rejected-completion retention, review feedback, and exact-hash materialization. Generated code is
never executed before source review. Approved-for-sandbox proposals run without network, as a
non-root user, with a read-only root and source mount, dropped capabilities, and CPU, memory, PID,
and temporary-filesystem limits.

The independent representative evaluator is
[`tools/evaluate_duplicate_finder.py`](../../tools/evaluate_duplicate_finder.py). It is deliberately
separate from model-generated tests and checks nested traversal, deterministic bytes, input hashes
and metadata, same-path and hard-link collision safety, output exclusion, symlink escape and cycle
handling, and unreadable-file evidence.

The result of this bootstrap is architectural line **v1.1**. It must not be called v2. The name v2
is reserved for a fresh challenger architecture produced in a later champion–challenger round.

## Winner rule

The bootstrap comparison is not a weighted score. Enhanced v1 wins only when:

1. every capability that passed in v1 still passes;
2. no v1 capability regresses;
3. at least one previously absent v2 capability passes with retained evidence.

This is a strict additive/Pareto rule. It does not mean the universal autonomous foundry is
complete. Missing automatic orchestration or L4 proof for arbitrary future tasks remains missing,
even when v1.1 wins this comparison.

## Retained result

The first isolated r4 run was rejected as the replacement because it discarded v1's evidence,
supply-chain, sandbox, contract, composition, provenance, and release gates. In the integrated run,
all of those gates remained present; 221 repository tests passed with 71.65% coverage; package,
security, provenance, notices, and system-E2E gates passed; the representative proposal passed 9
generated tests and 7 independent adversarial tests. The enhanced v1 branch therefore became
champion v1.1 under the rule above. A fresh challenger v2 has not yet won or replaced it.
