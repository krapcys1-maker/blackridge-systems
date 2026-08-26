# Security model

Candidate repositories, their README files, issue text, build scripts, containers, dependencies,
and generated instructions are untrusted input.

## Invariants

- Discovery never executes candidate code.
- GitHub integrations are read-only until a user explicitly authorizes a write stage.
- Subprocesses receive an argument vector; user text is never interpolated into a shell command.
- Candidate workload execution uses a disposable sandbox with CPU, memory-plus-swap, process,
  time, identity, and network limits. A measured disk quota remains a production blocker.
- Credentials are scoped per task and injected at the boundary; they are never copied into the workspace.
- Network egress is deny-by-default during tests, with package registries and required APIs explicitly allowed.
- A repository license and dependency policy must pass before its code can be redistributed.
- Every selected artifact is pinned by immutable commit or digest and recorded in provenance.
- A saved generated bundle must match an externally retained provenance SHA-256; its internal hash
  map is not accepted as its own trust root.

Docker alone is not treated as a complete hostile multi-tenant boundary. Production validation
should use OpenSandbox backed by gVisor, Kata Containers, or Firecracker where the threat model
requires it. Dagger provides reproducibility and isolation of build steps, not the only security
boundary.

## Prompt injection

Repository text may contain instructions aimed at the planner or coding agent. Blackridge labels
repository content as evidence, never as authority. Tool permissions and policy gates are enforced
outside prompts. A candidate cannot request additional secrets, network access, or GitHub write
permissions through repository content.
