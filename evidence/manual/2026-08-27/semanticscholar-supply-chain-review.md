# semanticscholar supply-chain review — 2026-08-27

## Verdict

`danielnsilva/semanticscholar` 0.12.0 is a materially cleaner literature-search candidate than
PaperQA, but it remains L0 and is not ready for promotion. Its exact release has an MIT repository
license, no direct dependency license concerns, and no vulnerabilities reported by the pinned
source-tree scan. It lacks an exact dependency lock, SBOM dependency edges, OpenSSF Scorecard
coverage, and PyPI provenance for both distribution files. Functional contract and sandbox tests
have not yet been run.

## Exact identity

- Repository: `danielnsilva/semanticscholar`.
- Signed tag: `v0.12.0`.
- Dereferenced commit: `3189ecb80bd985b6cd9b4a56fb410b05515f0f15`.
- Tree: `1e8944a90a0fb6f7257a41b31cf24ea80e25bb56`.
- PyPI release: `semanticscholar==0.12.0`.
- Checkout pristine before and after scanners: yes.
- Packaging metadata: statically read from `setup.py`; no project code was executed.

## Positive observations

- GitHub and static packaging metadata both report MIT.
- Direct dependency licenses: `httpx==0.28.1` BSD-3-Clause and `tenacity==9.1.4`
  Apache-2.0; zero concerns.
- OSV source-tree scan: zero vulnerable package entries and zero advisory IDs.
- The required `pypi-attestations==0.0.30` verifier was available.
- Pinned Syft and OSV containers exited and left no scanner containers running.

## Blocking observations

- `requirements.txt` and `setup.py` declare dependencies without a recognized exact lockfile.
  The observed resolved versions are therefore not a reproducible runtime closure.
- CycloneDX inventories 14 components but contains zero dependency edges, so reachability remains
  unknown.
- Some SBOM packages have no declared license data.
- OpenSSF status is `not-found`.
- PyPI provenance is missing for both `semanticscholar-0.12.0-py3-none-any.whl` and
  `semanticscholar-0.12.0.tar.gz`; PyPI also reports that Trusted Publishing was not used.
- No functional literature-search contract, live API behavior, rate-limit behavior, malformed
  response handling, or sandbox network policy has been reviewed.

## Evidence

- Probe SHA-256:
  `b3472ad096a39e610f8fa1d53c3d9155529f7701d107f58553ea5d3f687d782d`.
- SPDX raw SHA-256:
  `9afe914738a55b44337d2cea0528aed2e4c270d3116d8cf179956a25ac766b31`.
- SPDX stable inventory SHA-256:
  `f6c649ef0f1631d5deb9e30a43513636bb771274ffcf5d24c1dbee29fe3f2278`.
- CycloneDX raw SHA-256:
  `4cb2411295ae1ee4cb4e1deda503a1f604156a2911750eb49894c8204f1634b4`.
- CycloneDX stable inventory SHA-256:
  `2cd2a3f2df716dca71aa71f318ce20c3eb7d9b7ad675de38a627efd89930cc91`.
- OSV artifact SHA-256:
  `8466f4625e55dbd8ea4b7af88ce1ad239b292a5aa27a43f17851ceff1893f218`.
- Artifact root:
  `D:\Skladacz aplikacji\blackridge-experiments\alternative-semanticscholar-final`.

## System defects exposed and removed

The first attempt failed because the probe assumed every PyPI source used `pyproject.toml`. The
probe now reads `pyproject.toml`, `setup.cfg`, or a restricted static subset of `setup.py` without
executing foreign code. A hostile regression fixture proves that top-level `setup.py` statements
do not run.

The second attempt failed because the parser required a CycloneDX `dependencies` field even when
the generator omitted an empty collection. Required component/package inventories remain strict;
optional empty edge collections are now normalized safely. The final probe also reports absent
lockfiles and dependency edges explicitly instead of allowing a zero-vulnerability result to look
more complete than it is.

## Next experiment

Create a Blackridge-owned, hash-pinned lock for an isolated evaluation environment without
modifying the upstream source. Then implement a thin adapter and frozen contract cases for query,
paper retrieval, pagination, empty results, rate limiting, malformed upstream responses, and
network denial. Run live allow-listed calibration separately from deterministic replay and
networkless negative controls. Promotion remains forbidden until these results receive named
manual review.
