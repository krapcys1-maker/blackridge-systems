# Universal reuse-first software foundry

Build a local, provider-neutral software foundry. A user describes any software system in plain
language. The foundry must identify missing requirements, decompose the goal into replaceable
capabilities and contracts, discover several current implementations on GitHub and the public
internet, retain exact source identities, and distinguish search claims from measured behavior.

For every capability it must compare alternatives, inspect licenses and maintenance, and propose
which candidates deserve an isolated probe. It must prefer importing an upstream component behind
a stable CLI, package, API, MCP, or OCI boundary. It may write only small adapters or functionality
for which no suitable component survives verification.

The foundry must keep the intellectual operator replaceable. The initial operator may use a
DeepSeek-compatible JSON API, while a Codex session or another provider must be attachable through
the same logical contract. Provider output is a proposal, never proof that code works.

The deterministic control plane must constrain tools and budgets, pin revisions, validate every
structured artifact, retain inputs and outputs without secrets, enforce time and resource limits,
record failures, and require external evaluation before promotion. If a candidate fails, the
foundry must explain whether it should configure, adapt, repair, or replace it and then continue
from the affected stage.

The first deliverable is a runnable vertical slice that accepts a new brief, produces a validated
capability plan, performs live GitHub discovery, blocks a denied repository from being imported,
and emits a proposed generated-system bundle plus evidence. It must state honestly which later
verification, composition, repair, sandbox, and release gates are not implemented.
