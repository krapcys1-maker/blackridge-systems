# Scientific Researcher v1 — public builder specification

Build a command-line scientific researcher that reads one `research-request/v1` JSON object from
standard input and writes exactly one `research-output/v1` JSON object to standard output. Logs and
diagnostics belong on standard error.

The input contains a question, a minimum source count, and a corpus of identified documents. For an
answerable request, the system must synthesize an answer from the supplied full text, identify at
least the requested number of unique sources, attach citations to every factual claim, and quote
text that actually occurs in the cited document. It must never invent a document identity or title.
The corpus may contain plausible but irrelevant distractors. They must not be cited merely to reach
the minimum, and copying the complete corpus is not accepted as synthesis.

If the corpus cannot satisfy the requested minimum or does not support an answer, return
`insufficient-evidence` with no claims and no sources. A concise explanation is still required in
the `answer` field.

The exact public contracts are `research-input.schema.json` and `research-output.schema.json`. The
implementation language and architecture are unrestricted. Network services and LLMs may be used
only when the run plan allows them. The evaluator treats stdout as an artifact, so a zero process
exit code alone cannot pass.

Deliverables for a real A/B run:

1. source repository at an exact commit;
2. clean-install instructions and a machine-readable candidate command;
3. dependency lock or equivalent immutable pins;
4. build and run evidence with wall time, model usage/cost source, repair iteration count, generated
   source lines, reused source lines, and any unavailable measurement explicitly marked;
5. no access to the evaluator cases or expectations during construction.
