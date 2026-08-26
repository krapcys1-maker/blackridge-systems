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

Automated probes collect observations but never promote an evidence level by themselves. A named
manual reviewer must compare the retained artifact with a concrete acceptance scenario. Exit code
zero, a green CI job, an LLM judgment, or the presence of metadata is not functional proof.

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

Every promotion record retains the exact scenario, expected observations, actual observations,
reviewer, timestamp, evidence artifact, and caveats. Missing evidence remains unknown. Conflicting
sources remain a blocker until reconciled; one source is not silently preferred.

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

The active composer v1 deliberately solves a single-artifact linear path. Each required capability
has replaceable component options with one accepted and one produced contract. Options are removed
by hard license, integration, immutable-revision, evidence-level, review-hash, and policy gates.
The remaining Cartesian choices are routed deterministically through the fewest eligible adapters;
selection priority only breaks otherwise compatible choices and is retained in the definition.
Production mode requires at least L3 plus a valid named review and its still-matching raw probe.
Calibration mode may exercise explicitly marked L0 fixtures but can never become release-ready.

## Generated system layout

```text
generated-system/
  compose.yaml
  blackridge.blueprint.yaml
  composition.definition.yaml
  composition.plan.json
  components.lock.yaml
  adapters/
  contracts/
  tests/
  evidence/
  sbom/
  provenance.json
  README.md
```

The definition and complete solver plan are copied into the bundle so their provenance hashes can
be independently recomputed after the original workspace path disappears. The generator also
returns and prints the SHA-256 of `provenance.json`. Runners require this digest as an external
trust root; hashes stored only inside the bundle are not treated as proof against deliberate
relocking.

## Milestones

1. **Scout** — executable discovery, metadata enrichment, ranking, blueprint, and provenance.
2. **Inspector** — exact-commit license, SBOM, known-vulnerability, posture, and provenance probes
   are active; API extraction and policy resolution remain next.
3. **Experimenter** — disposable sandbox boot is active for pinned public GitHub repositories;
   generalized boot recipes, contract suites, and resource measurements remain next.
4. **Composer** — declarative JSON Patch adaptation and paired contract verification are active;
   linear compatibility solving, locked generation, provenance validation, and a shell-free
   calibration runtime are active; multi-input DAGs, sandbox-backed production execution,
   structural/AI adapter generation, and replacement loops remain next.
5. **Foundry** — reusable integration memory and delivery of verified systems from natural-language goals.

## Comparative benchmark boundary

Benchmark tests are authored and frozen before builder execution. Builders receive only the public
specification and output contract; the evaluator retains process observations and inspects the
artifact. A baseline and Blackridge run are comparable only when model identity, budgets, runtime,
network, secrets, starting state, benchmark bytes, and attempt policy match. Raw task success, time,
cost, repairs, clean installation, and code-reuse measurements remain visible without an automatic
weighted verdict. See [`benchmark-protocol.md`](benchmark-protocol.md).
