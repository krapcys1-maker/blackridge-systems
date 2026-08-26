# Blackridge Systems

**Build from what already works. Write only what is missing.**

Blackridge Systems is an evidence-driven system foundry. It turns a desired outcome into
capabilities, discovers strong open-source implementations for each capability, verifies that
they are legally and technically usable, adapts their seams, and tests the resulting system.

The product rule is:

> **Reuse → inspect → verify → adapt → integrate → test → build only the gap.**

Blackridge does not blindly merge repositories and does not treat GitHub stars as proof of
quality. Every candidate moves through explicit evidence levels before it may enter a generated
system.

## Current status

This repository contains an executable, manually reviewed vertical slice:

1. describe a system as capabilities with concrete acceptance scenarios;
2. search GitHub through the existing Octocode research engine;
3. enrich candidates with official GitHub metadata and OpenSSF Scorecard data;
4. apply license, maintenance, security, and adoption gates;
5. inspect real package versions and resolved dependency graphs through deps.dev;
6. separate raw probe evidence from named, explicit manual review;
7. build and exercise an exact public commit in disposable Docker through SWE-ReX;
8. adapt and validate JSON contracts with RFC 6902 and JSON Schema, including a broken control;
9. inspect an exact release independently with Syft, OSV-Scanner, Scorecard, deps.dev, GitHub,
   and PyPI Integrity;
10. freeze and calibrate an artifact-first A/B benchmark before either builder is run;
11. emit an auditable discovery run and a **provisional** system blueprint.

These probes produce evidence, not automatic approval. A candidate cannot be marked
production-ready until its concrete acceptance scenarios pass named manual review through L4.

```mermaid
flowchart LR
    A[Goal] --> B[Capability specification]
    B --> C[Discover repositories]
    C --> D[Inspect source and provenance]
    D --> E[License and security gates]
    E --> F[Sandbox experiments]
    F --> G[Compatibility graph]
    G --> H[Adapters]
    H --> I[Generated system]
    I --> J[End-to-end evaluation]
    J -- failed --> C
```

## Quick start

Prerequisites: Python 3.11+, Node.js 20+, GitHub CLI authenticated with `gh auth login`, and
Git. Docker is recommended now and becomes required for sandbox validation.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
python -m pip install -e ".[dev]"

blackridge doctor
blackridge discover examples/scientific-researcher.yaml --output .blackridge/research.json
blackridge report .blackridge/research.json
blackridge blueprint .blackridge/research.json --output .blackridge/blueprint.yaml

# A real package probe does not assign its own PASS/FAIL verdict.
blackridge probe-package pypi paper-qa --output .blackridge/evidence/paper-qa.json
blackridge review-probe examples/blackridge-self-hosting.yaml \
  .blackridge/evidence/paper-qa.json \
  --capability ecosystem-intelligence \
  --scenario paper-qa-package-evidence \
  --verdict pass \
  --reviewer "your-name" \
  --observed "Describe exactly what you inspected" \
  --notes "Explain why the evidence satisfies or fails the contract"
```

The discovery command invokes pinned upstream tooling as a subprocess with an argument vector,
never through an interpolated shell command. Remote repository code is not executed.

## Why this architecture

The difficult part is not finding popular repositories. It is proving that a precise component:

- implements the needed capability rather than merely claiming it;
- has a usable license and dependency chain;
- can be installed reproducibly;
- satisfies a stable input/output contract;
- works beside the other selected components;
- performs better than the alternatives on the user's actual workload.

Blackridge records those claims as evidence. Search results are only `L0 — discovered`; a
component needs at least `L2 — booted` before selection and `L4 — system verified` before a
system can be released.

## Upstream-first toolchain

Blackridge integrates tools at stable boundaries instead of copying their code:

| Stage | Selected upstream building block | Role |
| --- | --- | --- |
| discovery | Octocode + GitHub CLI/MCP | repository and code research |
| ecosystem intelligence | deps.dev | package versions, licenses, advisories, provenance, and resolved graphs |
| context | Repomix | bounded, reproducible repository snapshots |
| quality | OpenSSF Scorecard + OSV-Scanner/OSV-SCALIBR | posture and known vulnerabilities |
| compliance | OSS Review Toolkit + Syft | licenses, dependencies, and SBOMs |
| execution | SWE-ReX / Docker; OpenSandbox next | pinned disposable execution backend |
| experiments | Dagger | reproducible build and test DAGs |
| payload adaptation | JSON Patch + JSON Schema | declarative field mapping and contract evidence |
| adaptation | ast-grep / OpenRewrite | structural transforms instead of regex patches |
| missing code | OpenHands software-agent-sdk | write only adapters or genuinely missing modules |

Exact evaluated versions and integration status live in
[`upstream-catalog.yaml`](upstream-catalog.yaml). The landscape research and rejected options
are documented in [`docs/research-landscape.md`](docs/research-landscape.md).

### Run a real disposable repository probe

Install the optional adapter, build the pinned runtime, and execute the retained public-repository
scenario:

```powershell
python -m pip install --editable ".[sandbox]"
docker build --tag blackridge/swerex-runtime:1.4.0 --file docker/swerex-runtime.Dockerfile .
blackridge probe-environment examples/sandbox-pypa-sampleproject.yaml `
  --output .blackridge/evidence/sampleproject.json
```

This uses SWE-ReX and Docker, resolves the built image to its immutable local ID, applies resource
and process restrictions, fetches one 40-character Git commit, retains every command result, hashes
the host source tree before and after, and verifies container removal. It deliberately emits no
approval verdict. The paired negative scenario is
[`examples/sandbox-pypa-sampleproject-negative.yaml`](examples/sandbox-pypa-sampleproject-negative.yaml).

The Docker backend has outbound network access and is not presented as a hardened multi-tenant
sandbox. No host path is mounted during this probe. OpenSandbox with a stronger isolation backend
and egress policy remains the production boundary.

### Inspect one exact release across the supply chain

Pull the immutable scanner images listed in the experiment, then retain full SBOM and vulnerability
artifacts next to the raw probe:

```powershell
docker pull anchore/syft@sha256:678bfa565b60f747aac0f8e964fe5588a24445b8d0a480e91f6efd70020dfbb0
docker pull ghcr.io/google/osv-scanner@sha256:8108ae94eadea5a02c9bec6e646909d5b790b44bd62d7f5b7f0b1d6d0ffc7734
blackridge probe-supply-chain examples/supply-chain-paperqa.yaml `
  --work-root .blackridge/supply-chain/paperqa `
  --artifact-dir .blackridge/evidence/paperqa-artifacts `
  --output .blackridge/evidence/paperqa-supply-chain.json
```

The probe keeps repository and direct-dependency licensing, SBOM license coverage, Scorecard
posture, OSV findings, GitHub commit verification, and PyPI distribution provenance as separate
observations. OSV exit 1 is retained as a finding result. A missing source remains unknown.

### Calibrate the first A/B benchmark

```powershell
blackridge benchmark-calibrate `
  benchmarks/scientific-researcher-v1/evaluator/benchmark.yaml `
  benchmarks/scientific-researcher-v1/calibration-reference.yaml `
  benchmarks/scientific-researcher-v1/calibration-broken.yaml `
  --output .blackridge/evidence/benchmark-calibration.json
```

The evaluator reads candidate JSON from stdout rather than trusting exit code zero. It retains
individual critical checks and raw telemetry and deliberately emits no weighted success score.
The frozen two-arm procedure and contamination boundary are documented in
[`docs/benchmark-protocol.md`](docs/benchmark-protocol.md). Real baseline and Blackridge runs start
only after this harness is manually calibrated.

### Verify a component adapter and its negative control

```powershell
blackridge probe-adapter examples/adapter-paper-title-to-document-name.yaml `
  --output .blackridge/evidence/adapter.json
blackridge probe-composition examples/adapter-paper-title-to-document-name.yaml `
  examples/adapter-paper-title-broken.yaml `
  --output .blackridge/evidence/composition.json
```

The adapter is a reviewable RFC 6902 document, not generated Python. The pair command refuses to
compare different fixtures or schemas and retains both complete artifacts. In the supplied example,
both patches apply without an exception; JSON Schema validation is what detects the broken edge.

## Repository map

```text
src/blackridge/           deterministic control plane and CLI
examples/                 capability specifications
tests/                    unit tests with mocked external tools
evidence/manual/          retained real-world probes and named manual verdicts
docs/                     architecture, research, and security decisions
upstream-catalog.yaml     pinned upstream choices and provenance
```

## Scope boundary

Blackridge is not yet a one-command autonomous software factory. Discovery, package and
exact-release supply-chain inspection, commit-pinned Docker experiments, declarative payload
adapters, paired negative contract verification, and frozen benchmark calibration now produce raw
evidence. The first isolated A/B attempts, production-scope dependency classification,
compatibility solving, adapter synthesis beyond JSON Patch, hardened remote isolation through
OpenSandbox, and full generated-system delivery are next.

## License

Apache-2.0. Upstream components retain their own licenses and are not vendored into this
repository.
