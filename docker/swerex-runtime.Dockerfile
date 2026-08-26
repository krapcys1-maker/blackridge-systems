FROM python:3.12-slim@sha256:7a8b475003c4fe15a2cd4e55e5cfc2f3560bdc9333d624f24cdd6d4340fd7a17

LABEL org.opencontainers.image.title="Blackridge pinned SWE-ReX runtime"
LABEL org.opencontainers.image.source="https://github.com/krapcys1-maker/blackridge-systems"
LABEL org.opencontainers.image.licenses="Apache-2.0 AND LicenseRef-Third-Party"
LABEL org.opencontainers.image.base.name="python:3.12-slim"
LABEL org.opencontainers.image.base.digest="sha256:7a8b475003c4fe15a2cd4e55e5cfc2f3560bdc9333d624f24cdd6d4340fd7a17"
LABEL systems.blackridge.swerex.version="1.4.0"

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

# aiohttp is pinned explicitly because swe-rex 1.4.0 imports it in the Docker
# client without declaring it in the wheel metadata.
RUN python -m pip install --no-cache-dir \
    "aiohttp>=3.10,<4" \
    "swe-rex==1.4.0" \
    && swerex-remote --version

COPY LICENSE NOTICE THIRD_PARTY_NOTICES.md /usr/share/licenses/blackridge/
