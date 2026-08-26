# Release compliance and artifact boundaries

Blackridge treats the source tree, Python wheel, and OCI image as different distributions.
Their inventories must not be inferred from one another.

## Wheel

The wheel contains Blackridge source and its own `LICENSE`, `NOTICE`, and
`THIRD_PARTY_NOTICES.md`. Its `Requires-Dist` metadata names Python dependencies, but their
package bytes are not embedded in the wheel. `probe-wheel-release` extracts the built wheel,
compares the real metadata with `compliance/distribution-manifest.yaml`, runs the pinned Syft
image over the extracted bytes, and produces both SPDX and CycloneDX plus a license bundle.

## OCI image

The SWE-ReX runtime image physically distributes its base filesystem, Debian packages, Python
packages, and explicitly installed tools. `docker/distribution-manifest.yaml` records only what
the Dockerfile declares; it is intentionally not presented as the complete inventory.

`probe-image-release` accepts only an immutable image ID or repository digest. It then:

1. saves those exact image bytes and scans the archive with the pinned Syft OCI image;
2. inventories Python distributions and Debian binary-to-source package mappings;
3. extracts Python license/notice files and Debian `copyright` files;
4. creates SPDX, CycloneDX, JSON/TSV manifests, and `license-bundle.zip`;
5. retains unresolved distribution obligations as release blockers.

The v1 image is an internal build-and-test runtime and public image publication is prohibited.
Its base digest and Debian snapshot are fixed; the Dockerfile checks an exact 118-package dpkg
lock and installs the complete 35-distribution Python closure through exact versions and wheel
hashes. The exact-image probe independently compares the installed closure with both embedded
locks.

An exact technical review identifies four packages whose wheel metadata says `NOASSERTION` or
`UNKNOWN` by matching their extracted license-file hashes. That resolves metadata uncertainty but
is not public-distribution approval. The image still includes GPLv3+ `bashlex` and MPL-2.0
`certifi`, and a reviewed corresponding-source delivery mechanism for the complete relevant image
surface has not been attached. Those two facts keep the public image gate closed. A license text,
SBOM, upstream URL, or AI review alone does not resolve them.

## Apache-2.0 rule applied by Blackridge

Apache-2.0 section 4 requires recipients of a distributed work or derivative work to receive the
license, requires modified files to carry change notices, requires relevant notices to be retained,
and requires applicable upstream `NOTICE` attribution to remain readable. Blackridge therefore
keeps its own `NOTICE`, generates third-party notices from the active surface, and bundles the
actual license/notice files found in an image.

Primary references:

- https://www.apache.org/licenses/LICENSE-2.0
- https://www.apache.org/legal/apply-license
- https://github.com/anchore/syft/tree/v1.51.0
- https://github.com/docker-library/python

This machinery collects and blocks on evidence. It is not legal advice and does not replace a
qualified review before public distribution.

## Release workflow

`.github/workflows/release-evidence.yml` runs for version tags and manual dispatch. It builds the
wheel and internal image, generates artifact-specific evidence, and uploads that evidence before
policy enforcement. The v1 workflow succeeds only when the wheel gate is open, the internal image
probe completes with policy exit 1, and the public image gate remains closed as required. An
operational image-probe failure or an unexpectedly open image gate fails the workflow. The
workflow has read-only repository permissions and contains no image login, push, or release step.
