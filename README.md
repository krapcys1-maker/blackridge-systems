# Blackridge Systems

**An auditable control plane for assembling software from components you can prove things about.**

Blackridge turns a desired outcome into capabilities, finds candidate open-source implementations,
and — this is the part that matters — refuses to use any of them until their license, provenance,
supply chain, and behavior are recorded as evidence rather than assumed. It writes new code only
for the gap that no reviewed component fills.

The design rule is:

> **Reuse → inspect → verify → adapt → integrate → test → build only the gap.**

Stars are not quality. A green CI run is not proof. An exit code of zero is not a verdict. Every
candidate climbs an explicit evidence ladder (`L0 discovered` → `L4 system verified`), and a named
human review is required for the top of it.

## What this is today

Two things work, are tested, and are honest about their limits.

**1. An evidence and supply-chain control plane.** This is the mature part. It discovers
repositories through the GitHub CLI and Octocode, enriches them with deps.dev and OpenSSF
Scorecard, applies license/maintenance/security gates, inspects exact releases with Syft,
OSV-Scanner and PyPI Integrity, solves acyclic contract graphs with fan-out and fan-in, generates
provenance-locked runnable bundles, and executes them in a networkless non-root Docker sandbox with
enforced memory, CPU, PID, and timeout limits. Every boundary fails closed and retains raw
evidence. Composition **selection** is measured by
[`benchmarks/composition-reuse-v1`](benchmarks/composition-reuse-v1).

**2. A bounded code generator for the remaining gap.** A replaceable operator (DeepSeek today,
behind the provider-neutral `AgentBackend` protocol) proposes a program and its tests. The
deterministic control plane owns everything else: schemas, hashes, budgets, allowlists, repair
limits, and acceptance. The operator cannot promote its own output. On the frozen Duplicate Finder
workload this reached 3/3 with zero manual interventions at USD 0.030 per series.

Calibrated results on independent workloads are recorded, including the ones that failed. The
scientific claim auditor reproduces MultiVerS on SciFact against the **official** evaluator at
abstract label F1 `0.8385`, improved to `0.8608` by a bounded retrieval cascade; a cheaper ONNX NLI
candidate reached `0.4045` and was rejected rather than quietly dropped. See
[`benchmarks/scientific-claim-auditor-v1`](benchmarks/scientific-claim-auditor-v1).

## What this is not

Being precise here is cheaper than being discovered later.

- **Not an autonomous software factory.** L4 promotion requires a named human reviewer by design.
  A person is in the loop, not a temporary scaffold to be removed.
- **Not a general system generator.** The demonstrated generation workload is a single-file
  command-line utility of roughly 200 lines. Nothing here shows that an arbitrary application can
  be generated.
- **Not a proof that reuse scales.** The composition benchmark covers one capability and one
  reviewed implementation. Multi-capability routing and adapter synthesis beyond JSON Patch are
  unmeasured.
- **Not a legal or security certification.** A wheel policy pass is a technical artifact check. A
  passing supply-chain probe is evidence, not approval.
- **Not published.** No image or package from this repository is released for public consumption.

The evolution loop that produced the current champion is **closed**. Seven champion-challenger
rounds ran; the alternate ledger architecture lost all seven, and the generation workload saturated
at 3/3, so it can no longer separate candidates. History and reasoning are in
[`evolution/state.json`](evolution/state.json) and
[`evolution/rounds`](evolution/rounds); the successor measurement is the composition-reuse
benchmark above.

## Current capabilities

| Area | Status | Evidence |
| --- | --- | --- |
| Capability planning from a brief | working, provider-optional | `blackridge plan` |
| GitHub discovery with deny policy and query budget | working | `blackridge discover` |
| deps.dev / Scorecard / OSV / Syft / PyPI Integrity probes | working | `blackridge probe-supply-chain` |
| Evidence ladder with named manual review | working | `blackridge review-probe` |
| Pinned disposable Docker experiments (SWE-ReX) | working | `blackridge probe-environment` |
| Contract graph solving, fan-out and fan-in | working | `blackridge compose-solve` |
| Provenance-locked bundle generation and execution | working | `blackridge probe-composer` |
| Networkless non-root sandbox with resource limits | working | `blackridge compose-run-sandbox` |
| Component selection under license/evidence/hash gates | **measured** | `tools/evaluate_composition_reuse.py` |
| Bounded gap generation with fail-closed repair | working, narrow | `blackridge propose-gap` |
| Adapter synthesis beyond JSON Patch | not implemented | — |
| Multi-capability composition at scale | not measured | — |
| Autonomous L4 promotion | out of scope by design | — |

The full numbered list of implemented boundaries, including every negative control, is in
[`docs/architecture.md`](docs/architecture.md). Exact evaluated upstream versions and their
integration status are in [`upstream-catalog.yaml`](upstream-catalog.yaml); rejected options are
documented in [`docs/research-landscape.md`](docs/research-landscape.md).

The gated path toward a scientific researcher and an evidence-controlled research loop is in
[`docs/research-loop-roadmap.md`](docs/research-loop-roadmap.md). It is a roadmap. It is not a claim
that Blackridge performs autonomous scientific discovery.

## Quick start

Prerequisites: Python 3.11+, Node.js 20+, GitHub CLI authenticated with `gh auth login`, and
Git. Docker is recommended now and becomes required for sandbox validation.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
python -m pip install -e ".[dev]"

blackridge doctor
blackridge plan path/to/brief.md --output .blackridge/system-request.yaml
blackridge discover .blackridge/system-request.yaml --provider github \
  --deny-repository owner/repository --output .blackridge/discovery.json
blackridge propose-gap path/to/brief.md --request .blackridge/system-request.yaml \
  --discovery .blackridge/discovery.json --output .blackridge/proposal.json
blackridge report .blackridge/discovery.json
blackridge blueprint .blackridge/discovery.json --output .blackridge/blueprint.yaml

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

`blackridge plan` currently supports DeepSeek through `DEEPSEEK_API_KEY`. The core boundary is the
`AgentBackend` protocol rather than a provider SDK. In the interactive workflow, the current Codex
subscription session remains the human-supervised intellectual operator; Blackridge does not need
an OpenAI API key. A directly callable Codex subscription backend is not claimed by this release.

The retained Blackridge 2.0 self-hosting protocol and Pareto comparison live in
[`benchmarks/blackridge-self-hosting-v2`](benchmarks/blackridge-self-hosting-v2). The isolated r4
prototype did not beat v1, so its evidence was frozen and the prototype runner was removed. The
planner, operator boundary, official GitHub discovery, deny policy, bounded repair feedback and
hash-gated materialization were then integrated into the v1 control plane. This produced the
architectural champion **v1.1**, packaged as `0.1.1`, not a replacement v2. The repeated workload
passed 9 generated tests plus 7 independent adversarial tests in the pinned sandbox while all v1
gates remained green. It is not a claim that the complete autonomous universal foundry is finished.

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

The gated path from the foundry to a scientific researcher, AI-memory experiments, and an
evidence-controlled research loop is recorded in
[`docs/research-loop-roadmap.md`](docs/research-loop-roadmap.md). It is a roadmap, not a claim that
Blackridge already performs autonomous scientific discovery.

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
python -m pip install -e ".[supply-chain]"
blackridge probe-supply-chain examples/supply-chain-paperqa.yaml `
  --work-root .blackridge/supply-chain/paperqa `
  --artifact-dir .blackridge/evidence/paperqa-artifacts `
  --output .blackridge/evidence/paperqa-supply-chain.json
```

The probe keeps repository and direct-dependency licensing, SBOM license coverage, Scorecard
posture, OSV findings, GitHub commit verification, and PyPI distribution provenance as separate
observations. OSV exit 1 is retained as a finding result. A missing source remains unknown.
The pinned `pypi-attestations` verifier distinguishes provenance availability from cryptographic
artifact/repository verification. PyPI provenance does not by itself bind the release to the exact
requested source commit, so that limitation remains explicit in the evidence.

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

An independently authored blinded suite can be byte-verified without interpreting its cases via
`blackridge verify-holdout`. The external manifest format and separation rules are documented in
[`docs/sealed-holdout.md`](docs/sealed-holdout.md).

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

### Solve, generate, and execute a compatible system

```powershell
blackridge compose-solve examples/composition-linear-calibration.yaml `
  --output .blackridge/composition-plan.yaml
blackridge compose-generate examples/composition-linear-calibration.yaml `
  .blackridge/composition-plan.yaml .blackridge/generated-research
blackridge compose-run .blackridge/generated-research examples/composition-input.json `
  --provenance-sha256 <hash-printed-by-compose-generate> `
  --output .blackridge/composition-output.json `
  --evidence .blackridge/evidence/composition-run.json
```

The v1 solver uses hard gates, not a hidden weighted score. It checks allowed licenses and
integration modes, exact command, resource, and adapter hashes, evidence levels, named review
hashes in production mode, and a complete acyclic contract graph. Independent branches may fan out
from one artifact and a later component may consume their contract-keyed outputs together. The
generator copies code and declared non-code resources into the bundle, writes component and
evidence locks, the exact definition and full plan, schemas, adapter definitions, a shell-free
runtime, provenance hashes, an SBOM gate, and an explicit `release_ready: false`.

Definitions may set bounded `sandbox_resources` (`memory_mb`, `cpus`, and `pids`). The selected
values are copied into `runtime.yaml`, covered by provenance, applied to Docker, and checked against
the live cgroup before any component executes. Defaults remain 1024 MiB, 2 CPUs, and 256 processes.
Definitions may also lock a `sandbox_image` with an immutable image reference and expected local
image ID. When present, the sandbox rejects a caller-supplied alternative or a resolved ID mismatch
before creating a container.
Host execution is calibration-only. A component-specific dependency image and its distribution
evidence are still separate release surfaces; generated code and data do not imply that an
arbitrary host Python already contains the component's dependencies.

Large bundled resources also carry an explicit `copy_timeout_seconds` control (300 seconds by
default, configurable from 5 to 1800). The value is copied into both the component lock and
`runtime.yaml`; the sandbox rejects an invalid or relocked value instead of relying on a hidden
global transfer timeout.

The SHA-256 printed by `compose-generate` is an external trust root. Saved bundles cannot execute
by merely rewriting `runtime.yaml` and updating the hashes inside their own `provenance.json`;
`compose-run` and `compose-run-sandbox` require that independently retained digest.
Both runtimes retain failure evidence but publish the normal `--output` artifact only after every
step and output contract completes successfully. Host calibration also verifies every component
launch hash before executing the first step, so a known-bad later component cannot cause a partial
pipeline run.

### Audit source provenance and release artifacts

```powershell
blackridge probe-source-provenance provenance/source-audit.yaml `
  --output .blackridge/evidence/source-provenance.json
blackridge check-provenance provenance/derived-code.yaml
blackridge compliance-notices --check
blackridge probe-wheel-release dist/blackridge_systems-0.1.1-py3-none-any.whl
blackridge probe-image-release `
  blackridge/swerex-runtime@sha256:<exact-repository-digest>
```

The source scan combines Git first-add history with an exact normalized six-line comparison against
pinned upstream trees. It does not claim that zero exact matches proves originality. The copy gate
requires source and destination hashes, an immutable upstream commit, license text, compatibility
decision, attribution, modification notes, and a hash-locked named manual review.

The wheel and image are separate distribution surfaces. The image probe inventories the exact
digest, extracts Python and Debian license material, and remains blocked while corresponding-source
or other license obligations are unresolved. See
[`docs/release-compliance.md`](docs/release-compliance.md) and
[`docs/source-provenance-policy.md`](docs/source-provenance-policy.md).

## Repository map

```text
src/blackridge/           deterministic control plane and CLI
examples/                 capability specifications
tests/                    deterministic unit and integration regression tests
tools/system_e2e.py       installed-wheel host/Docker E2E and fail-closed controls used by CI
evidence/manual/          retained real-world probes and named manual verdicts
benchmarks/               independent workloads, including composition-reuse-v1
evolution/                closed champion-challenger history and frozen benchmark specs
components/               reviewed components with contracts and dependency locks
docs/                     architecture, research, and security decisions
upstream-catalog.yaml     pinned upstream choices and provenance
compliance/               active distribution manifest used to generate notices
provenance/               source-audit scope and fail-closed derived-code registry
docker/                   runtime Dockerfile and declared image component manifest
```

## Scope boundary

Blackridge is not a one-command autonomous software factory, and the gap is specific rather than
vague.

**Producing raw evidence today:** discovery, package and exact-release supply-chain inspection,
commit-pinned Docker experiments, declarative payload adapters, paired negative contract
verification, frozen benchmark calibration, multi-input DAG solving, hash-locked resource bundling,
locked generation, component selection under license/evidence/hash gates, and host/sandbox
calibration runtime execution.

**Not built:** adapter synthesis beyond JSON Patch, production-scope dependency images, production
sandbox execution, hardened remote isolation through OpenSandbox, and L4 delivery.

**Deliberately human:** L4 promotion. A named reviewer compares the retained artifact against a
concrete acceptance scenario. Removing that step would remove the only thing separating this from a
code generator with good logging.

## License

Apache-2.0. Upstream components retain their own licenses and are not vendored into this
repository.
