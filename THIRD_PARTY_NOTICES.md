# Third-Party Software Notices

Blackridge Systems is licensed under Apache-2.0. Third-party components retain their
own licenses. This file is generated from `compliance/distribution-manifest.yaml`; it
does not relicense those components and it is not a substitute for their license texts.

A Blackridge wheel declares dependencies but does not embed their packages. The Docker
image is a separate distribution: its complete package inventory, SBOM, copyright files,
and license bundle must be generated from the exact image digest for every release.

## Python dependencies declared by the wheel

### HTTPX (>=0.27,<1)

- License: `BSD-3-Clause`
- Upstream: https://github.com/encode/httpx
- License text: https://github.com/encode/httpx/blob/0.28.1/LICENSE.md
- Usage: HTTP clients for deps.dev, OpenSSF Scorecard, PyPI, and GitHub-facing probes.
- Distributed in: wheel dependency metadata

### jsonschema (4.26.0)

- License: `MIT`
- Upstream: https://github.com/python-jsonschema/jsonschema/tree/v4.26.0
- License text: https://github.com/python-jsonschema/jsonschema/blob/v4.26.0/COPYING
- Usage: Draft 2020-12 component and artifact contract validation.
- Distributed in: wheel dependency metadata

### Pydantic (>=2.10,<3)

- License: `MIT`
- Upstream: https://github.com/pydantic/pydantic
- License text: https://github.com/pydantic/pydantic/blob/v2.13.4/LICENSE
- Usage: Strict request, evidence, composition, and compliance models.
- Distributed in: wheel dependency metadata

### python-json-patch (1.33)

- License: `BSD-3-Clause`
- Upstream: https://github.com/stefankoegl/python-json-patch/tree/v1.33
- License text: https://github.com/stefankoegl/python-json-patch/blob/v1.33/LICENSE
- Usage: RFC 6902 payload adaptation through the public Python API.
- Distributed in: wheel dependency metadata

### PyYAML (>=6,<7)

- License: `MIT`
- Upstream: https://github.com/yaml/pyyaml
- License text: https://github.com/yaml/pyyaml/blob/6.0.3/LICENSE
- Usage: Declarative request, benchmark, composition, and policy documents.
- Distributed in: wheel dependency metadata

### Rich (>=13.9,<15)

- License: `MIT`
- Upstream: https://github.com/Textualize/rich
- License text: https://github.com/Textualize/rich/blob/v14.3.4/LICENSE
- Usage: Human-readable CLI output.
- Distributed in: wheel dependency metadata

### Typer (>=0.15,<1)

- License: `MIT`
- Upstream: https://github.com/fastapi/typer
- License text: https://github.com/fastapi/typer/blob/0.27.1/LICENSE
- Usage: Blackridge command-line interface.
- Distributed in: wheel dependency metadata


## External command-line integrations (not embedded)

### GitHub CLI (environment prerequisite)

- License: `MIT`
- Upstream: https://github.com/cli/cli
- License text: https://github.com/cli/cli/blob/trunk/LICENSE
- Usage: Authenticated GitHub metadata and commit/license inspection.
- Distributed in: none

### Octocode (npm:octocode@18.3.0)

- License: `MIT`
- Upstream: https://github.com/bgauryy/octocode/tree/af20f667fd2536c9502f69d99fe6bdedfcc839cb
- License text: https://github.com/bgauryy/octocode/blob/af20f667fd2536c9502f69d99fe6bdedfcc839cb/LICENSE
- Usage: Repository discovery invoked as a pinned npx CLI.
- Distributed in: none


## External service integrations (not embedded)

### deps.dev API (v3)

- License: `Apache-2.0`
- Upstream: https://github.com/google/deps.dev
- License text: https://github.com/google/deps.dev/blob/main/LICENSE
- Usage: Package, dependency, advisory, and license metadata over HTTPS.
- Distributed in: none

### OpenSSF Scorecard API (public API)

- License: `Apache-2.0`
- Upstream: https://github.com/ossf/scorecard
- License text: https://github.com/ossf/scorecard/blob/main/LICENSE
- Usage: Repository security-posture observations over HTTPS.
- Distributed in: none


## Build and inspection tools (not embedded in the wheel)

### OSV-Scanner (v2.5.1 OCI digest 8108ae94eade)

- License: `Apache-2.0`
- Upstream: https://github.com/google/osv-scanner/tree/v2.5.1
- License text: https://github.com/google/osv-scanner/blob/v2.5.1/LICENSE
- Usage: Vulnerability inspection of retained CycloneDX SBOMs.
- Distributed in: none

### Syft (v1.51.0 OCI digest 678bfa565b60)

- License: `Apache-2.0`
- Upstream: https://github.com/anchore/syft/tree/v1.51.0
- License text: https://github.com/anchore/syft/blob/v1.51.0/LICENSE
- Usage: SPDX and CycloneDX generation from exact release artifacts.
- Distributed in: none


## Declared Docker base

### Python slim OCI base (sha256:7a8b475003c4fe15a2cd4e55e5cfc2f3560bdc9333d624f24cdd6d4340fd7a17)

- License: `LicenseRef-MultiLicense-Image`
- Upstream: https://github.com/docker-library/python
- License text: generated exact-image license-bundle.zip
- Usage: Base operating system and CPython runtime for the SWE-ReX image.
- Distributed in: SWE-ReX runtime image


## Packages explicitly installed in the Docker runtime

### aiohttp (>=3.10,<4)

- License: `Apache-2.0 AND MIT`
- Upstream: https://github.com/aio-libs/aiohttp
- License text: generated exact-image license-bundle.zip
- Usage: Explicit SWE-ReX Docker-client dependency missing from swe-rex 1.4.0 metadata.
- Distributed in: SWE-ReX runtime image

### Debian ca-certificates (resolved during image build)

- License: `LicenseRef-MultiLicense-Debian-ca-certificates`
- Upstream: https://packages.debian.org/ca-certificates
- License text: generated exact-image license-bundle.zip
- Usage: TLS trust store installed by the Dockerfile.
- Distributed in: SWE-ReX runtime image

### Debian Git (resolved during image build)

- License: `LicenseRef-MultiLicense-Debian-Git`
- Upstream: https://packages.debian.org/git
- License text: generated exact-image license-bundle.zip
- Usage: Fetch exact candidate commits inside disposable environments.
- Distributed in: SWE-ReX runtime image

### SWE-ReX (1.4.0)

- License: `MIT`
- Upstream: https://github.com/SWE-agent/SWE-ReX/tree/f802b3e14d82aa4c13291d2fda5bd4fd48f36f91
- License text: https://github.com/SWE-agent/SWE-ReX/blob/f802b3e14d82aa4c13291d2fda5bd4fd48f36f91/LICENSE.txt
- Usage: Replaceable Python SDK and CLI execution boundary for disposable environments.
- Distributed in: SWE-ReX runtime image

## Release rule

Do not publish an image from this repository unless its exact-digest compliance probe
has produced both SPDX and CycloneDX SBOMs, the package manifests, the extracted
license texts, and the archive containing them. The generated evidence must have
no unresolved release blockers. An SBOM does not replace license texts,
attribution, source-code obligations, or a license-compatibility review.
