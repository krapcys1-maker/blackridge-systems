FROM python:3.12-slim@sha256:7a8b475003c4fe15a2cd4e55e5cfc2f3560bdc9333d624f24cdd6d4340fd7a17

LABEL org.opencontainers.image.title="Blackridge pinned SWE-ReX runtime"
LABEL org.opencontainers.image.source="https://github.com/krapcys1-maker/blackridge-systems"
LABEL org.opencontainers.image.licenses="Apache-2.0 AND LicenseRef-Third-Party"
LABEL org.opencontainers.image.base.name="python:3.12-slim"
LABEL org.opencontainers.image.base.digest="sha256:7a8b475003c4fe15a2cd4e55e5cfc2f3560bdc9333d624f24cdd6d4340fd7a17"
LABEL systems.blackridge.swerex.version="1.4.0"
LABEL systems.blackridge.debian.snapshot="20260824T000000Z"

# The base image records these exact Debian snapshot timestamps in its source file comments.
# Reusing them freezes both the direct Git package and its apt-resolved dependency closure.
RUN sed -i \
      -e 's|http://deb.debian.org/debian$|https://snapshot.debian.org/archive/debian/20260824T000000Z|' \
      -e 's|http://deb.debian.org/debian-security$|https://snapshot.debian.org/archive/debian-security/20260824T000000Z|' \
      /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Check-Valid-Until=false update \
    && apt-get install --yes --no-install-recommends 'git=1:2.47.3-0+deb13u1' \
    && test "$(dpkg-query -W -f='${Version}' ca-certificates)" = '20250419' \
    && rm -rf /var/lib/apt/lists/*

COPY docker/os-packages.lock.tsv /usr/share/blackridge/locks/os-packages.lock.tsv
RUN dpkg-query -W \
      '-f=${binary:Package}\t${Version}\t${source:Package}\t${source:Version}\n' \
      > /tmp/os-packages.actual.tsv \
    && tail -n +3 /usr/share/blackridge/locks/os-packages.lock.tsv \
      > /tmp/os-packages.expected.tsv \
    && diff --unified /tmp/os-packages.expected.tsv /tmp/os-packages.actual.tsv \
    && rm /tmp/os-packages.actual.tsv /tmp/os-packages.expected.tsv

# SWE-ReX 1.4.0 omits aiohttp from its metadata even though its Docker client imports it.
# The lock therefore contains the complete observed closure rather than trusting transitive
# resolution during the build.
COPY docker/python-requirements.lock /usr/share/blackridge/locks/python-requirements.lock
RUN python -m pip install --root-user-action=ignore --no-cache-dir --only-binary=:all: \
      --require-hashes --requirement /usr/share/blackridge/locks/python-requirements.lock \
    && python -m pip check \
    && swerex-remote --version

COPY LICENSE NOTICE THIRD_PARTY_NOTICES.md /usr/share/licenses/blackridge/
