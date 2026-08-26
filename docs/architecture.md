# Architecture

## Product definition

Blackridge Systems is a capability-driven software composition system. Its unit of selection is
not a repository. It is a **capability implementation behind a contract**.

A repository may contribute one component, several components, a CLI process, an MCP server, an
OCI image, an API service, or only an implementation pattern. Blackridge should prefer stable
public boundaries over extracting internal source files.

## Control flow

```text
goal
  -> capability planner
  -> repository/package discovery
  -> source and metadata inspection
  -> legal/security gates
  -> reproducible sandbox boot
  -> component contract tests
  -> pairwise compatibility graph
  -> architecture solver
  -> adapter generation
  -> end-to-end workload evaluation
  -> provenance-locked generated system
```

The solver may replace any failing candidate and rerun only affected graph branches. It writes new
code only when no candidate satisfies the contract or when a small adapter is cheaper and safer
than a replacement.

An L0 blueprint keeps a primary inspection target plus four alternatives for each capability. It
must not collapse the search space to one repository before sandbox and contract evidence exists.

## Evidence ladder

| Level | Name | Required proof |
| --- | --- | --- |
| L0 | discovered | search result and repository identity |
| L1 | inspected | source/API surface, dependency graph, license, and security findings |
| L2 | booted | pinned revision installs and starts in a bounded sandbox |
| L3 | contract-tested | component passes its capability contract and neighbor compatibility tests |
| L4 | system-verified | complete system passes the user's representative end-to-end workload |

An L0 ranking is a queue for inspection, not a selection. Stars are a weak adoption prior. A
candidate becomes selectable at L2 and releasable only at L4.

## Scoring model

The MVP emits a transparent provisional score from observable metadata:

- search relevance prior: 25%;
- maintenance recency: 20%;
- adoption: 10%;
- contributor/community proxy: 5%;
- issue pressure: 5%;
- license confidence: 15%;
- OpenSSF security posture: 20%.

Future L1-L4 stages will dominate this metadata prior with measured functional fit, performance,
reliability, resource use, integration cost, and end-to-end task success. A hard gate can reject a
high-scoring candidate.

## Compatibility graph

Every component contract declares:

```yaml
accepts:
  - scholarly-query/v1
produces:
  - paper-record/v1
non_functional:
  max_latency_ms: 5000
  network: outbound-https
  data_classification: public
```

Edges represent tested transformations, not architectural guesses. A generated adapter records
its upstream inputs, prompt/model if AI-generated, tests, license decision, and exact revision.

## Generated system layout

```text
generated-system/
  compose.yaml
  blackridge.blueprint.yaml
  components.lock.yaml
  adapters/
  contracts/
  tests/
  evidence/
  sbom/
  provenance.json
  README.md
```

## Milestones

1. **Scout** — executable discovery, metadata enrichment, ranking, blueprint, and provenance.
2. **Inspector** — commit-pinned source snapshots, API extraction, SBOM, license, and security gates.
3. **Experimenter** — disposable sandboxes, boot recipes, contract tests, and resource measurements.
4. **Composer** — compatibility graph, solver, adapter generation, and alternative replacement loop.
5. **Foundry** — reusable integration memory and delivery of verified systems from natural-language goals.
