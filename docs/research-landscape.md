# Research landscape — 2026-08-26

This snapshot used GitHub repository metadata, upstream READMEs, release data, and OpenSSF
Scorecard where available. Popularity is not treated as correctness; numbers only show maturity
and current activity at the snapshot date.

## Finding

No mature open-source project was found that reliably performs the complete loop:

```text
natural-language goal -> capability decomposition -> cross-repository discovery
-> license/security evaluation -> empirical compatibility tests -> adapter generation
-> verified generated system
```

The gap is real, but strong components exist for almost every stage. Blackridge should integrate
those components and own the evidence model, compatibility solver, and feedback loop.

## Shortlist

| Project | Observed signal | Decision |
| --- | --- | --- |
| bgauryy/octocode | 920 stars, MIT, pushed 2026-08-25; GitHub/local search, AST and LSP | primary discovery engine |
| github/github-mcp-server | 32.5k stars, MIT, active; official tool allowlists and read-only mode | official GitHub boundary |
| google/deps.dev | public API v3; seven package ecosystems, resolved graphs, licenses, advisories, and provenance | active package-intelligence provider; never assume complete registry coverage |
| yamadashy/repomix | 28k stars, MIT, active; remote commit packing and safe remote-config default | reproducible source snapshot |
| ossf/scorecard | 5.6k stars, Apache-2.0, v5.5.0, Scorecard 9.0 | repository security posture |
| google/osv-scalibr | Apache-2.0, plugin-oriented SCA library | package and vulnerability inventory |
| google/osv-scanner | Apache-2.0, v2.5.1, official pinned OCI image | active SBOM vulnerability scanner |
| oss-review-toolkit/ort | 2k stars, Apache-2.0, 20k+ commits | compliance and policy pipeline |
| anchore/syft | 9.4k stars, Apache-2.0, v1.51.0 | SBOM generation |
| opensandbox-group/OpenSandbox | 14.7k stars, Apache-2.0, active | preferred hostile-code sandbox platform |
| SWE-agent/SWE-ReX | MIT; local, Docker, and cloud execution interface | useful runner abstraction/alternative |
| dagger/dagger | 16.2k stars, Apache-2.0, active | reproducible experiment DAGs |
| ast-grep/ast-grep | 15.6k stars, MIT, active | structural adapter transformations |
| OpenHands/software-agent-sdk | MIT, v1.43.1, active | coding fallback for missing seams |

## First scientific-researcher vertical slice

The broader search changed the initial component map. Filtering everything to Python would discard
strong service-based components and violates the language-neutral adapter idea.

- **PaperQA2** is the strongest first candidate for scientific retrieval and grounded evidence:
  Apache-2.0, 9.1k stars, active, packaged for Python, and already combines scientific paper
  search, metadata providers, retrieval, evidence ranking, and citation-rich answers.
- **GROBID** is the stronger scholarly parser seed despite being Java. It has an Apache-2.0
  license, 5.1k stars, an active 0.9.1 release, multiple maintainers, and a service boundary.
  Blackridge should adapt its REST contract instead of reimplementing the parser in Python.
- **Semantic Scholar client/FastMCP server** provide replaceable API and citation-network
  boundaries. The client is smaller and more mature; the MCP server has the more useful native
  agent contract but needs stronger L2/L3 testing.
- **AllenAI Asta Theorizer** directly addresses literature-grounded theory generation and ships
  predictive-accuracy, novelty, and belief evaluations under Apache-2.0. It is still a research
  system with several API keys and 30–60 minute jobs, so it belongs behind a costly sandbox gate.
- **Inspect AI + inspect_evals** are strong evaluation seeds. Domain-specific scientific checks
  still need to be authored as Blackridge contracts; a generic LLM judge is not enough.

## Projects from the original idea

### CodeNav

AllenAI CodeNav demonstrates navigation and reuse from an unfamiliar codebase and is licensed
Apache-2.0. It is a valuable research reference. It was not selected as the production search
engine because the observed last push was 2024-08-21, it depends on Elasticsearch, and its own
documentation warns that it can execute arbitrary code and is unsuitable for security-sensitive
use without a sandbox.

### CodeTeam

CodeTeam's Architect → CTO → Developer → QA organization is relevant to NL2Repo generation.
However, the observed repository had 3 stars and no detected license. Blackridge can borrow the
idea of competing architecture proposals, but must not copy or depend on unlicensed code.

### Replen

Replen is the closest product signal for recommending repositories and estimating integration
effort against an existing project. At the snapshot it was active and Apache-2.0 but very early
(8 stars). It remains a benchmark and potential future provider rather than a core dependency.

## New findings beyond the original list

OpenSandbox is a stronger foundation for untrusted candidate execution than building a Docker
wrapper from scratch: it exposes multi-language SDKs, a sandbox protocol, Docker/Kubernetes
runtimes, egress controls, and stronger isolation backends.

SWE-ReX separates agent logic from execution infrastructure and already supports parallel shell
environments. It is useful when the experiment layer needs to move between local Docker and cloud
providers.

The v1.4.0 wheel passed installation but its Docker client import failed until `aiohttp` was added;
the wheel does not declare that import. Its default Docker startup also falls back to
`pipx run swe-rex` without a version constraint when the image lacks `swerex-remote`. Blackridge
therefore pins both client dependencies and builds `swerex-remote==1.4.0` into a base-digest-pinned
runtime image before resolving that built image to its immutable local ID.

OSV-SCALIBR, ORT, Scorecard, and Syft cover complementary slices. One tool should not be forced to
answer every supply-chain question: Scorecard evaluates project practice; SCALIBR inventories and
finds vulnerabilities; ORT enforces license/policy; Syft emits SBOMs.

The exact PaperQA v2026.08.12 experiment confirmed why these observations must remain separate.
Syft produced SPDX and CycloneDX inventories but all 305 SPDX package license fields were unknown;
OSV-Scanner found 23 vulnerable package entries in the full lockfile scope; Scorecard had no record;
and PyPI exposed no provenance for either distribution file. The system retained the release as
blocked instead of converting a successful scan or repository Apache-2.0 license into approval.

ast-grep and OpenRewrite make adapter work reviewable and repeatable. The system should generate a
structural rewrite recipe where possible instead of a one-off text patch.

For payload-contract mismatches, a source rewrite is unnecessary. `python-json-patch` v1.33 gives
Blackridge a compact RFC 6902 boundary, while `jsonschema` v4.26.0 supplies Draft 2020-12 validation
and complete error iteration. A real chained `add {}` plus `copy` probe exposed that the JSON Patch
working operation value can be mutated; Blackridge now deep-copies the declared operations and
retains separate definition/working-copy mutation observations.

deps.dev materially expands discovery beyond GitHub, but real probes exposed two important limits:
package license metadata can disagree with repository metadata, and registry coverage may lag an
upstream release. It is an evidence source, not an authority that may silently override conflicting
facts or substitute an older version.

## Rejected architecture shortcuts

- **Clone the top-starred repos and merge them.** Repository boundaries, licenses, runtimes, and
  data contracts make this unreliable.
- **Use one giant coding agent.** It loses provenance and tends to reimplement existing code.
- **Treat an LLM judge as the final verifier.** Functional claims require executable contract and
  end-to-end tests.
- **Run candidate setup scripts on the host.** Repository content is untrusted.
- **Vendor every upstream source tree.** Prefer SDK, CLI, MCP, API, or OCI boundaries and immutable
  pins so updates and licenses remain manageable.
