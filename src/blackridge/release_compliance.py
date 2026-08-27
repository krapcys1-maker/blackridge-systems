"""Artifact-specific notices, SBOMs, license bundles, and release blockers."""

from __future__ import annotations

import email
import hashlib
import json
import os
import re
import shutil
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from blackridge.errors import BlackridgeError
from blackridge.evidence import ProbeEvidence
from blackridge.formats import load_yaml
from blackridge.process_boundary import run_bounded

RELEASE_COMPLIANCE_SOURCE = (
    "https://github.com/krapcys1-maker/blackridge-systems/"
    "blob/main/src/blackridge/release_compliance.py"
)
DEFAULT_SYFT_IMAGE = (
    "anchore/syft@sha256:678bfa565b60f747aac0f8e964fe5588a24445b8d0a480e91f6efd70020dfbb0"
)
_EXACT_IMAGE = re.compile(r"^(?:sha256:[a-f0-9]{64}|.+@sha256:[a-f0-9]{64})$")
_REVIEW_LICENSE = re.compile(r"(?i)(?:^|[^A-Z])(A?GPL|LGPL|MPL|EPL|CDDL|SSPL)")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _object_list(value: object, context: str) -> list[dict[str, object]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise BlackridgeError(f"{context} must be a list of JSON objects")
    return value


def _requirement_name(requirement: str) -> str:
    match = re.match(r"^[A-Za-z0-9_.-]+", requirement)
    if match is None:
        raise BlackridgeError(f"wheel contains an invalid Requires-Dist value: {requirement!r}")
    return match.group(0).casefold().replace("_", "-")


def _run(
    argv: list[str],
    *,
    cwd: Path | None = None,
    accepted: set[int] | None = None,
) -> dict[str, object]:
    completed = run_bounded(argv, cwd=cwd)
    result = {
        "argv": argv,
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "timed_out": completed.timed_out,
        "output_limit_exceeded": completed.output_limit_exceeded,
        "stdout_bytes_seen": completed.stdout_bytes_seen,
        "stderr_bytes_seen": completed.stderr_bytes_seen,
    }
    if completed.timed_out:
        raise BlackridgeError(f"command timed out: {argv[0]}")
    if completed.output_limit_exceeded:
        raise BlackridgeError(f"command exceeded the output limit: {argv[0]}")
    if completed.returncode not in (accepted or {0}):
        raise BlackridgeError(
            f"command failed with exit {completed.returncode}: {argv[0]} {completed.stderr.strip()}"
        )
    return result


def _docker_user_args() -> list[str]:
    """Make bind-mounted outputs writable without running as container root on POSIX."""

    getuid = getattr(os, "getuid", None)
    getgid = getattr(os, "getgid", None)
    if os.name == "posix" and callable(getuid) and callable(getgid):
        return ["--user", f"{getuid()}:{getgid()}"]
    return []


class StrictModel(BaseModel):
    """Reject misspelled compliance controls instead of silently ignoring them."""

    model_config = ConfigDict(extra="forbid")


class NoticeComponent(StrictModel):
    """A component actually used by Blackridge, with its distribution boundary."""

    id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    name: str
    package_name: str | None = None
    version: str
    license_spdx: str
    upstream: str
    license_source: str
    usage: str
    boundary: Literal[
        "declared-wheel-dependency",
        "external-cli",
        "external-api",
        "build-or-inspection-tool",
        "docker-base",
        "docker-installed-package",
    ]
    distributed_in: list[str] = Field(default_factory=list)


class DistributionManifest(StrictModel):
    """Declared release surface; inventories still come from built artifacts."""

    schema_version: Literal["1"] = "1"
    project: str
    project_license: str
    repository: str
    components: list[NoticeComponent]


class ReviewedLicenseFile(StrictModel):
    """One license file identified in an exact installed distribution."""

    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ReviewedSourceArchive(StrictModel):
    """Primary source archive metadata retained for an exact package release."""

    filename: str = Field(min_length=1)
    url: str = Field(pattern=r"^https://")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class PythonPackageLicenseReview(StrictModel):
    """Technical license identification tied to exact package and license bytes."""

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    observed_metadata: str = Field(min_length=1)
    concluded_license_spdx: str = Field(min_length=1)
    requires_public_distribution_review: bool
    license_files: list[ReviewedLicenseFile] = Field(min_length=1)
    sources: list[str] = Field(min_length=1)
    source_archive: ReviewedSourceArchive


class PythonLicenseReview(StrictModel):
    """Engineering review; public approval requires a separately named qualified reviewer."""

    schema_version: Literal["1"] = "1"
    reviewer: str = Field(min_length=3)
    review_scope: str = Field(min_length=20)
    public_distribution_approved: bool = False
    qualified_reviewer: str | None = None
    packages: list[PythonPackageLicenseReview] = Field(min_length=1)

    @model_validator(mode="after")
    def approval_requires_qualified_reviewer(self) -> PythonLicenseReview:
        if self.public_distribution_approved and not self.qualified_reviewer:
            raise ValueError("public approval requires qualified_reviewer")
        return self


def load_distribution_manifest(path: Path) -> DistributionManifest:
    return DistributionManifest.model_validate(load_yaml(path))


def load_python_license_review(path: Path) -> PythonLicenseReview:
    return PythonLicenseReview.model_validate(load_yaml(path))


def render_third_party_notices(manifest: DistributionManifest) -> str:
    """Render deterministic notices only from the declared active distribution surface."""

    sections = {
        "declared-wheel-dependency": "Python dependencies declared by the wheel",
        "external-cli": "External command-line integrations (not embedded)",
        "external-api": "External service integrations (not embedded)",
        "build-or-inspection-tool": "Build and inspection tools (not embedded in the wheel)",
        "docker-base": "Declared Docker base",
        "docker-installed-package": "Packages explicitly installed in the Docker runtime",
    }
    lines = [
        "# Third-Party Software Notices",
        "",
        "Blackridge Systems is licensed under Apache-2.0. Third-party components retain their",
        "own licenses. This file is generated from `compliance/distribution-manifest.yaml`; it",
        "does not relicense those components and it is not a substitute for their license texts.",
        "",
        "A Blackridge wheel declares dependencies but does not embed their packages. The Docker",
        "image is a separate distribution: its complete package inventory, SBOM, copyright files,",
        "and license bundle must be generated from the exact image digest for every release.",
    ]
    grouped: dict[str, list[NoticeComponent]] = {key: [] for key in sections}
    for component in manifest.components:
        grouped[component.boundary].append(component)
    for boundary, title in sections.items():
        entries = sorted(grouped[boundary], key=lambda item: item.name.casefold())
        if not entries:
            continue
        lines.extend(["", f"## {title}", ""])
        for item in entries:
            distribution = ", ".join(item.distributed_in) if item.distributed_in else "none"
            lines.extend(
                [
                    f"### {item.name} ({item.version})",
                    "",
                    f"- License: `{item.license_spdx}`",
                    f"- Upstream: {item.upstream}",
                    f"- License text: {item.license_source}",
                    f"- Usage: {item.usage}",
                    f"- Distributed in: {distribution}",
                    "",
                ]
            )
    lines.extend(
        [
            "## Release rule",
            "",
            "Do not publish an image from this repository unless its exact-digest compliance probe",
            "has produced both SPDX and CycloneDX SBOMs, the package manifests, the extracted",
            "license texts, and the archive containing them. The generated evidence must have",
            "no unresolved release blockers. An SBOM does not replace license texts,",
            "attribution, source-code obligations, or a license-compatibility review.",
            "",
        ]
    )
    return "\n".join(lines)


def write_or_check_notices(
    manifest: DistributionManifest, path: Path, *, check: bool
) -> tuple[bool, str]:
    rendered = render_third_party_notices(manifest)
    if check:
        actual = path.read_text(encoding="utf-8") if path.is_file() else ""
        return actual == rendered, rendered
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered, encoding="utf-8", newline="\n")
    return True, rendered


def _safe_extract_wheel(wheel: Path, destination: Path) -> list[str]:
    names: list[str] = []
    root = destination.resolve()
    with zipfile.ZipFile(wheel) as archive:
        for member in archive.infolist():
            target = (destination / member.filename).resolve()
            if not target.is_relative_to(root):
                raise BlackridgeError(f"wheel contains an unsafe path: {member.filename}")
            names.append(member.filename)
        archive.extractall(destination)
    return names


def _syft_scan(
    source: str, *, scan_mount: Path, output_dir: Path, image: str
) -> list[dict[str, object]]:
    scan_mount = scan_mount.resolve()
    output_dir = output_dir.resolve()
    commands: list[dict[str, object]] = []
    for filename, format_name in (
        ("sbom.spdx.json", "spdx-json"),
        ("sbom.cdx.json", "cyclonedx-json"),
    ):
        commands.append(
            _run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges",
                    *_docker_user_args(),
                    "--tmpfs",
                    # Container-private tmpfs; no host temporary path is used.
                    "/tmp:rw,nosuid,nodev,size=512m,mode=1777",  # nosec B108
                    "-e",
                    "HOME=/tmp",
                    "-e",
                    "SYFT_CHECK_FOR_APP_UPDATE=false",
                    "-v",
                    f"{scan_mount}:/scan:ro",
                    "-v",
                    f"{output_dir}:/out",
                    image,
                    source,
                    "-o",
                    f"{format_name}=/out/{filename}",
                ]
            )
        )
    return commands


def _zip_directory(source: Path, output: Path) -> None:
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(item for item in source.rglob("*") if item.is_file()):
            archive.write(path, path.relative_to(source).as_posix())


def probe_wheel_release(
    wheel: Path,
    manifest: DistributionManifest,
    *,
    output_dir: Path,
    syft_image: str = DEFAULT_SYFT_IMAGE,
) -> ProbeEvidence:
    """Inspect the bytes actually contained in one wheel and build its compliance bundle."""

    wheel = wheel.resolve()
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise BlackridgeError("wheel release output directory must be empty")
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="blackridge-wheel-") as temporary:
        extracted = Path(temporary)
        members = _safe_extract_wheel(wheel, extracted)
        metadata_files = list(extracted.glob("*.dist-info/METADATA"))
        if len(metadata_files) != 1:
            raise BlackridgeError(
                f"wheel must contain exactly one METADATA file; found {len(metadata_files)}"
            )
        parsed = email.message_from_bytes(metadata_files[0].read_bytes())
        requirements = parsed.get_all("Requires-Dist") or []
        license_members = sorted(
            value
            for value in members
            if re.search(r"(?i)(?:^|/)(?:licenses?/|license|copying|notice)", value)
        )
        if not license_members:
            raise BlackridgeError("wheel does not contain a license file")
        commands = _syft_scan(
            "dir:/scan", scan_mount=extracted, output_dir=output_dir, image=syft_image
        )
        license_root = output_dir / "license-bundle"
        license_root.mkdir(parents=True, exist_ok=True)
        for member in license_members:
            source = extracted / member
            if source.is_file():
                target = license_root / "wheel" / member
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        (license_root / "THIRD_PARTY_NOTICES.md").write_text(
            render_third_party_notices(manifest), encoding="utf-8", newline="\n"
        )
        _zip_directory(license_root, output_dir / "license-bundle.zip")

    declared = sorted(
        (item.package_name or item.name).casefold().replace("_", "-")
        for item in manifest.components
        if item.boundary == "declared-wheel-dependency"
    )
    runtime_requirements = [item for item in requirements if "extra ==" not in item]
    optional_requirements = [item for item in requirements if "extra ==" in item]
    observed_names = sorted(_requirement_name(requirement) for requirement in runtime_requirements)
    missing = sorted(set(declared) - set(observed_names))
    undeclared = sorted(set(observed_names) - set(declared))
    spdx = json.loads((output_dir / "sbom.spdx.json").read_text(encoding="utf-8"))
    component_manifest = {
        "schema_version": "1",
        "artifact": str(wheel),
        "sha256": _sha256(wheel),
        "size": wheel.stat().st_size,
        "member_count": len(members),
        "embedded_license_files": license_members,
        "requires_dist": requirements,
        "runtime_requires_dist": runtime_requirements,
        "optional_requires_dist": optional_requirements,
        "declared_dependency_names": declared,
        "missing_declared_dependencies": missing,
        "unexpected_declared_dependencies": undeclared,
        "sbom_package_count": len(spdx.get("packages") or []),
    }
    manifest_path = output_dir / "wheel-components.json"
    manifest_path.write_text(
        json.dumps(component_manifest, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    blockers = []
    if missing or undeclared:
        blockers.append("wheel dependency metadata differs from the release manifest")
    return ProbeEvidence(
        probe_id=uuid4().hex,
        observed_at=datetime.now(UTC),
        provider="blackridge-wheel-release-compliance/1+syft/1.51.0",
        subject=f"{wheel.name}@sha256:{component_manifest['sha256']}",
        request={"wheel": str(wheel), "syft_image": syft_image},
        observations={
            "probe_completed": True,
            "component_manifest": component_manifest,
            "artifacts": {
                name: {
                    "sha256": _sha256(output_dir / name),
                    "size": (output_dir / name).stat().st_size,
                }
                for name in [
                    "sbom.spdx.json",
                    "sbom.cdx.json",
                    "license-bundle.zip",
                    "wheel-components.json",
                ]
            },
            "commands": commands,
            "release_blockers": blockers,
            "release_gate_open": not blockers,
        },
        sources=[RELEASE_COMPLIANCE_SOURCE, "https://www.apache.org/licenses/LICENSE-2.0"],
        warnings=[
            "A wheel dependency declaration does not embed or redistribute the dependency package."
        ],
    )


_IMAGE_COLLECTOR = r"""
import hashlib
import importlib.metadata as md
import json
import pathlib
import re
import shutil
import subprocess

out = pathlib.Path("/out")
license_root = out / "license-bundle"
records = []
for dist in md.distributions():
    name = dist.metadata.get("Name") or "unknown"
    version = dist.version
    license_value = (
        dist.metadata.get("License-Expression")
        or dist.metadata.get("License")
        or "NOASSERTION"
    )
    copied = []
    for item in dist.files or []:
        relative = pathlib.Path(str(item))
        lowered = relative.name.lower()
        license_names = ("license", "copying", "notice", "copyright", "authors")
        if not any(token in lowered for token in license_names):
            continue
        source = pathlib.Path(dist.locate_file(item))
        if not source.is_file():
            continue
        target = license_root / "python" / f"{name}-{version}" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append(str(relative))
    records.append({
        "name": name,
        "version": version,
        "license": license_value,
        "license_files": sorted(copied),
    })
(out / "python-packages.json").write_text(
    json.dumps(sorted(records, key=lambda x: x["name"].casefold()), indent=2) + "\n"
)

format_value = "${binary:Package}\t${Version}\t${source:Package}\t${source:Version}\n"
rows = subprocess.run(
    ["dpkg-query", "-W", "-f=" + format_value],
    check=True,
    capture_output=True,
    text=True,
).stdout
header = "binary_package\tbinary_version\tsource_package\tsource_version\n"
(out / "os-packages.tsv").write_text(header + rows)
os_records = []
for row in rows.splitlines():
    values = (row.split("\t") + ["", "", "", ""])[:4]
    binary_name, binary_version, source_name, source_version = values
    source_name = source_name or binary_name.split(":", 1)[0]
    source_version = source_version or binary_version
    copyright_file = pathlib.Path("/usr/share/doc") / binary_name.split(":", 1)[0] / "copyright"
    copied = None
    if copyright_file.is_file():
        target = license_root / "debian" / binary_name.replace(":", "_") / "copyright"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(copyright_file, target)
        copied = str(target.relative_to(license_root))
    os_records.append({
        "binary_package": binary_name,
        "binary_version": binary_version,
        "source_package": source_name,
        "source_version": source_version,
        "copyright_file": copied,
    })
(out / "os-source-manifest.json").write_text(json.dumps(os_records, indent=2) + "\n")

lock_root = pathlib.Path("/usr/share/blackridge/locks")
python_lock = lock_root / "python-requirements.lock"
os_lock = lock_root / "os-packages.lock.tsv"
for source in (python_lock, os_lock):
    if source.is_file():
        shutil.copy2(source, out / source.name)

canonical = lambda value: re.sub(r"[-_.]+", "-", value).casefold()
requirement_pattern = re.compile(
    r"^([A-Za-z0-9_.-]+)==([^\s]+)\s+--hash=sha256:([a-f0-9]{64})$"
)
locked_python = {}
python_parse_errors = []
if python_lock.is_file():
    for number, line in enumerate(python_lock.read_text().splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = requirement_pattern.fullmatch(stripped)
        if not match:
            python_parse_errors.append({"line": number, "value": stripped})
            continue
        name, version, digest = match.groups()
        key = canonical(name)
        if key in locked_python:
            python_parse_errors.append({"line": number, "value": "duplicate:" + key})
            continue
        locked_python[key] = {"name": name, "version": version, "sha256": digest}

actual_python = {canonical(item["name"]): item for item in records}
python_missing = sorted(set(locked_python) - set(actual_python))
python_unexpected = sorted(set(actual_python) - set(locked_python))
python_version_mismatches = [
    {
        "name": key,
        "locked": locked_python[key]["version"],
        "actual": actual_python[key]["version"],
    }
    for key in sorted(set(locked_python) & set(actual_python))
    if locked_python[key]["version"] != actual_python[key]["version"]
]
python_matches = bool(python_lock.is_file()) and not (
    python_parse_errors or python_missing or python_unexpected or python_version_mismatches
)

os_lock_lines = os_lock.read_text().splitlines() if os_lock.is_file() else []
snapshot_match = re.search(
    r"snapshot\s+([0-9]{8}T[0-9]{6}Z)", os_lock_lines[0] if os_lock_lines else ""
)
snapshot = snapshot_match.group(1) if snapshot_match else None
locked_os_rows = os_lock_lines[2:] if len(os_lock_lines) >= 2 else []
actual_os_rows = rows.splitlines()
os_matches = bool(os_lock.is_file()) and locked_os_rows == actual_os_rows
os_first_difference = None
for number in range(max(len(locked_os_rows), len(actual_os_rows))):
    expected = locked_os_rows[number] if number < len(locked_os_rows) else None
    actual = actual_os_rows[number] if number < len(actual_os_rows) else None
    if expected != actual:
        os_first_difference = {"row": number + 1, "locked": expected, "actual": actual}
        break

apt_sources_path = pathlib.Path("/etc/apt/sources.list.d/debian.sources")
apt_sources = apt_sources_path.read_text() if apt_sources_path.is_file() else ""
apt_snapshot_matches = bool(snapshot) and all(
    value in apt_sources
    for value in (
        f"https://snapshot.debian.org/archive/debian/{snapshot}",
        f"https://snapshot.debian.org/archive/debian-security/{snapshot}",
    )
) and "URIs: http://deb.debian.org" not in apt_sources

runtime_locks = {
    "schema_version": "1",
    "python": {
        "path": str(python_lock),
        "sha256": hashlib.sha256(python_lock.read_bytes()).hexdigest()
        if python_lock.is_file()
        else None,
        "locked_count": len(locked_python),
        "actual_count": len(actual_python),
        "parse_errors": python_parse_errors,
        "missing": python_missing,
        "unexpected": python_unexpected,
        "version_mismatches": python_version_mismatches,
        "matches": python_matches,
    },
    "os": {
        "path": str(os_lock),
        "sha256": hashlib.sha256(os_lock.read_bytes()).hexdigest()
        if os_lock.is_file()
        else None,
        "locked_count": len(locked_os_rows),
        "actual_count": len(actual_os_rows),
        "first_difference": os_first_difference,
        "matches": os_matches,
    },
    "apt_sources": {
        "path": str(apt_sources_path),
        "sha256": hashlib.sha256(apt_sources.encode()).hexdigest() if apt_sources else None,
        "snapshot": snapshot,
        "matches": apt_snapshot_matches,
    },
}
runtime_locks["complete"] = all(
    (
        runtime_locks["python"]["matches"],
        runtime_locks["os"]["matches"],
        runtime_locks["apt_sources"]["matches"],
    )
)
(out / "runtime-locks.json").write_text(json.dumps(runtime_locks, indent=2) + "\n")
"""


def _license_analysis(
    spdx: dict[str, object],
    python_packages: list[dict[str, object]],
    os_packages: list[dict[str, object]],
) -> dict[str, object]:
    packages = _object_list(spdx.get("packages"), "SPDX packages")
    spdx_unknown = [
        {"name": item.get("name"), "version": item.get("versionInfo")}
        for item in packages
        if item.get("licenseDeclared") in {None, "NONE", "NOASSERTION"}
    ]
    python_unknown = [
        item
        for item in python_packages
        if item.get("license") in {None, "", "UNKNOWN", "NOASSERTION"}
    ]
    python_without_text = [item for item in python_packages if not item.get("license_files")]
    python_review = [
        item for item in python_packages if _REVIEW_LICENSE.search(str(item.get("license") or ""))
    ]
    os_without_text = [item for item in os_packages if not item.get("copyright_file")]
    return {
        "sbom_package_count": len(packages),
        "sbom_without_declared_license_count": len(spdx_unknown),
        "sbom_without_declared_license": spdx_unknown,
        "python_package_count": len(python_packages),
        "python_unknown_license_metadata": python_unknown,
        "python_without_extracted_license_text": python_without_text,
        "python_review_license_packages": python_review,
        "os_package_count": len(os_packages),
        "os_without_extracted_copyright_file": os_without_text,
    }


def _canonical_package_name(value: object) -> str:
    return re.sub(r"[-_.]+", "-", str(value)).casefold()


def _verify_python_license_review(
    review: PythonLicenseReview,
    python_packages: list[dict[str, object]],
    license_root: Path,
) -> dict[str, object]:
    actual = {_canonical_package_name(item.get("name")): item for item in python_packages}
    valid: dict[str, PythonPackageLicenseReview] = {}
    entries: list[dict[str, object]] = []
    issues: list[str] = []
    seen: set[str] = set()
    python_root = (license_root / "python").resolve()

    for item in review.packages:
        key = _canonical_package_name(item.name)
        entry_issues: list[str] = []
        if key in seen:
            entry_issues.append("duplicate review entry")
        seen.add(key)
        observed = actual.get(key)
        if observed is None:
            entry_issues.append("package is absent from the exact image")
        else:
            if observed.get("version") != item.version:
                entry_issues.append(
                    f"version mismatch: review={item.version} image={observed.get('version')}"
                )
            if observed.get("license") != item.observed_metadata:
                entry_issues.append(
                    "metadata mismatch: "
                    f"review={item.observed_metadata} image={observed.get('license')}"
                )
            observed_license_files = observed.get("license_files")
            if not isinstance(observed_license_files, list) or not all(
                isinstance(value, str) for value in observed_license_files
            ):
                entry_issues.append("image license_files is not a list of paths")
                observed_files: set[str] = set()
            else:
                observed_files = set(observed_license_files)
            package_root = (
                python_root / f"{observed.get('name')}-{observed.get('version')}"
            ).resolve()
            for reviewed_file in item.license_files:
                relative = Path(reviewed_file.path)
                target = (package_root / relative).resolve()
                if relative.is_absolute() or not target.is_relative_to(package_root):
                    entry_issues.append(f"unsafe license path: {reviewed_file.path}")
                elif reviewed_file.path not in observed_files:
                    entry_issues.append(f"license path not reported by image: {reviewed_file.path}")
                elif not target.is_file():
                    entry_issues.append(f"license file is absent: {reviewed_file.path}")
                elif _sha256(target) != reviewed_file.sha256:
                    entry_issues.append(f"license hash mismatch: {reviewed_file.path}")
            if _REVIEW_LICENSE.search(str(observed.get("license") or "")) and not (
                item.requires_public_distribution_review
            ):
                entry_issues.append("reciprocal license was not marked for public review")
        if entry_issues:
            issues.extend(f"{item.name}=={item.version}: {issue}" for issue in entry_issues)
        else:
            valid[key] = item
        entries.append(
            {
                "name": item.name,
                "version": item.version,
                "concluded_license_spdx": item.concluded_license_spdx,
                "requires_public_distribution_review": (item.requires_public_distribution_review),
                "valid": not entry_issues,
                "issues": entry_issues,
                "sources": item.sources,
                "source_archive": item.source_archive.model_dump(),
            }
        )

    unknown = [
        item
        for item in python_packages
        if item.get("license") in {None, "", "UNKNOWN", "NOASSERTION"}
    ]
    unresolved_unknown = [
        item for item in unknown if _canonical_package_name(item.get("name")) not in valid
    ]
    reciprocal = [
        item for item in python_packages if _REVIEW_LICENSE.search(str(item.get("license") or ""))
    ]
    reciprocal_without_valid_review = [
        item for item in reciprocal if _canonical_package_name(item.get("name")) not in valid
    ]
    public_distribution_ready = bool(
        reciprocal
        and review.public_distribution_approved
        and review.qualified_reviewer
        and not reciprocal_without_valid_review
    )
    return {
        "schema_version": "1",
        "reviewer": review.reviewer,
        "review_scope": review.review_scope,
        "public_distribution_approved": review.public_distribution_approved,
        "qualified_reviewer": review.qualified_reviewer,
        "entries": entries,
        "issues": issues,
        "valid_entry_count": len(valid),
        "unresolved_unknown_metadata": unresolved_unknown,
        "reciprocal_license_packages": reciprocal,
        "reciprocal_without_valid_review": reciprocal_without_valid_review,
        "public_distribution_ready": public_distribution_ready,
    }


def _runtime_lock_blockers(runtime_locks: dict[str, object]) -> list[str]:
    if runtime_locks.get("complete") is True:
        return []
    return ["Dockerfile apt or Python dependency closure does not match its embedded locks"]


def probe_image_release(
    image_ref: str,
    manifest: DistributionManifest,
    *,
    output_dir: Path,
    syft_image: str = DEFAULT_SYFT_IMAGE,
    license_review_file: Path | None = None,
) -> ProbeEvidence:
    """Inventory one exact image, extract license texts, and expose unresolved obligations."""

    if not _EXACT_IMAGE.fullmatch(image_ref):
        raise BlackridgeError(
            "image release inspection requires an immutable image ID or repo digest"
        )
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise BlackridgeError("image release output directory must be empty")
    review = None
    if license_review_file is not None:
        license_review_file = license_review_file.resolve()
        review = load_python_license_review(license_review_file)
    output_dir.mkdir(parents=True, exist_ok=True)
    inspect = json.loads(str(_run(["docker", "image", "inspect", image_ref])["stdout"]))[0]
    observed_id = str(inspect.get("Id") or "")
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", observed_id):
        raise BlackridgeError("Docker did not return an immutable image ID")
    commands: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="blackridge-image-") as temporary:
        archive = Path(temporary) / "image.tar"
        commands.append(_run(["docker", "image", "save", "--output", str(archive), observed_id]))
        commands.extend(
            _syft_scan(
                "docker-archive:/scan/image.tar",
                scan_mount=Path(temporary),
                output_dir=output_dir,
                image=syft_image,
            )
        )
    collection_dir = output_dir
    commands.append(
        _run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                *_docker_user_args(),
                "-v",
                f"{collection_dir}:/out",
                observed_id,
                "python",
                "-c",
                _IMAGE_COLLECTOR,
            ]
        )
    )
    license_root = output_dir / "license-bundle"
    (license_root / "THIRD_PARTY_NOTICES.md").write_text(
        render_third_party_notices(manifest), encoding="utf-8", newline="\n"
    )
    spdx = json.loads((output_dir / "sbom.spdx.json").read_text(encoding="utf-8"))
    python_packages = json.loads((output_dir / "python-packages.json").read_text(encoding="utf-8"))
    os_packages = json.loads((output_dir / "os-source-manifest.json").read_text(encoding="utf-8"))
    runtime_locks = json.loads((output_dir / "runtime-locks.json").read_text(encoding="utf-8"))
    analysis = _license_analysis(spdx, python_packages, os_packages)
    if license_review_file is not None and review is not None:
        review_analysis = _verify_python_license_review(review, python_packages, license_root)
        review_copy = output_dir / "python-license-review.yaml"
        shutil.copy2(license_review_file, review_copy)
        bundled_review = license_root / "blackridge" / "python-license-review.yaml"
        bundled_review.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(license_review_file, bundled_review)
        review_analysis["review_file_sha256"] = _sha256(review_copy)
    else:
        review_analysis = {
            "schema_version": "1",
            "issues": ["no exact Python license review was supplied"],
            "valid_entry_count": 0,
            "unresolved_unknown_metadata": analysis["python_unknown_license_metadata"],
            "reciprocal_license_packages": analysis["python_review_license_packages"],
            "reciprocal_without_valid_review": analysis["python_review_license_packages"],
            "public_distribution_ready": False,
            "review_file_sha256": None,
        }
    _zip_directory(license_root, output_dir / "license-bundle.zip")
    blockers: list[str] = []
    if review_analysis["unresolved_unknown_metadata"]:
        blockers.append("Python packages have unknown license metadata")
    if analysis["python_without_extracted_license_text"]:
        blockers.append("Python packages are missing extracted license/notice text")
    if (
        review_analysis["reciprocal_license_packages"]
        and not review_analysis["public_distribution_ready"]
    ):
        blockers.append("Reciprocal-license packages require qualified public-distribution review")
    if analysis["os_without_extracted_copyright_file"]:
        blockers.append("Debian packages are missing extracted copyright files")
    blockers.append(
        "Corresponding source archives or a reviewed source-offer mechanism are not bundled"
    )
    blockers.extend(_runtime_lock_blockers(runtime_locks))
    manifest_data = {
        "schema_version": "1",
        "requested_image": image_ref,
        "observed_image_id": observed_id,
        "repo_digests": inspect.get("RepoDigests") or [],
        "created": inspect.get("Created"),
        "base_image": next(
            (item.model_dump() for item in manifest.components if item.boundary == "docker-base"),
            None,
        ),
        "license_analysis": analysis,
        "python_license_review": review_analysis,
        "runtime_locks": runtime_locks,
        "release_blockers": blockers,
        "release_gate_open": False,
    }
    image_manifest_path = output_dir / "image-components.json"
    image_manifest_path.write_text(
        json.dumps(manifest_data, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    artifacts = {}
    artifact_names = [
        "sbom.spdx.json",
        "sbom.cdx.json",
        "python-packages.json",
        "os-packages.tsv",
        "os-source-manifest.json",
        "runtime-locks.json",
        "python-requirements.lock",
        "os-packages.lock.tsv",
        "image-components.json",
        "license-bundle.zip",
    ]
    if license_review_file is not None:
        artifact_names.append("python-license-review.yaml")
    for name in artifact_names:
        path = output_dir / name
        artifacts[name] = {"sha256": _sha256(path), "size": path.stat().st_size}
    return ProbeEvidence(
        probe_id=uuid4().hex,
        observed_at=datetime.now(UTC),
        provider="blackridge-image-release-compliance/1+syft/1.51.0",
        subject=f"{observed_id}",
        request={"image_ref": image_ref, "syft_image": syft_image},
        observations={
            "probe_completed": True,
            "image_manifest": manifest_data,
            "artifacts": artifacts,
            "commands": commands,
            "release_blockers": blockers,
            "release_gate_open": False,
        },
        sources=[
            RELEASE_COMPLIANCE_SOURCE,
            "https://www.apache.org/licenses/LICENSE-2.0",
            "https://github.com/anchore/syft/tree/v1.51.0",
        ],
        warnings=[
            "The exact image is intentionally blocked until every listed obligation is resolved."
        ],
    )
