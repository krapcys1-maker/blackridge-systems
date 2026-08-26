"""Artifact-specific notices, SBOMs, license bundles, and release blockers."""

from __future__ import annotations

import email
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

import yaml
from pydantic import BaseModel, Field

from blackridge.errors import BlackridgeError
from blackridge.evidence import ProbeEvidence

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


def _run(
    argv: list[str],
    *,
    cwd: Path | None = None,
    accepted: set[int] | None = None,
) -> dict[str, object]:
    completed = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
    result = {
        "argv": argv,
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
    if completed.returncode not in (accepted or {0}):
        raise BlackridgeError(
            f"command failed with exit {completed.returncode}: {argv[0]} {completed.stderr.strip()}"
        )
    return result


def _docker_user_args() -> list[str]:
    """Make bind-mounted outputs writable without running as container root on POSIX."""

    if os.name == "posix" and hasattr(os, "getuid") and hasattr(os, "getgid"):
        return ["--user", f"{os.getuid()}:{os.getgid()}"]
    return []


class NoticeComponent(BaseModel):
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


class DistributionManifest(BaseModel):
    """Declared release surface; inventories still come from built artifacts."""

    schema_version: Literal["1"] = "1"
    project: str
    project_license: str
    repository: str
    components: list[NoticeComponent]


def load_distribution_manifest(path: Path) -> DistributionManifest:
    return DistributionManifest.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


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
                    "/tmp:rw,nosuid,nodev,size=512m,mode=1777",
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
    observed_names = sorted(
        re.match(r"^[A-Za-z0-9_.-]+", requirement).group(0).casefold().replace("_", "-")
        for requirement in runtime_requirements
    )
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
import importlib.metadata as md
import json
import pathlib
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
"""


def _license_analysis(
    spdx: dict[str, object],
    python_packages: list[dict[str, object]],
    os_packages: list[dict[str, object]],
) -> dict[str, object]:
    packages = [item for item in spdx.get("packages") or [] if isinstance(item, dict)]
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


def probe_image_release(
    image_ref: str,
    manifest: DistributionManifest,
    *,
    output_dir: Path,
    syft_image: str = DEFAULT_SYFT_IMAGE,
) -> ProbeEvidence:
    """Inventory one exact image, extract license texts, and expose unresolved obligations."""

    if not _EXACT_IMAGE.fullmatch(image_ref):
        raise BlackridgeError(
            "image release inspection requires an immutable image ID or repo digest"
        )
    output_dir = output_dir.resolve()
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
    analysis = _license_analysis(spdx, python_packages, os_packages)
    _zip_directory(license_root, output_dir / "license-bundle.zip")
    blockers: list[str] = []
    if analysis["python_unknown_license_metadata"]:
        blockers.append("Python packages have unknown license metadata")
    if analysis["python_without_extracted_license_text"]:
        blockers.append("Python packages are missing extracted license/notice text")
    if analysis["python_review_license_packages"]:
        blockers.append("Python packages use licenses that require an explicit distribution review")
    if analysis["os_without_extracted_copyright_file"]:
        blockers.append("Debian packages are missing extracted copyright files")
    blockers.append(
        "Corresponding source archives or a reviewed source-offer mechanism are not bundled"
    )
    blockers.append(
        "Dockerfile apt and transitive Python package resolution are not completely locked"
    )
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
        "release_blockers": blockers,
        "release_gate_open": False,
    }
    image_manifest_path = output_dir / "image-components.json"
    image_manifest_path.write_text(
        json.dumps(manifest_data, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    artifacts = {}
    for name in [
        "sbom.spdx.json",
        "sbom.cdx.json",
        "python-packages.json",
        "os-packages.tsv",
        "os-source-manifest.json",
        "image-components.json",
        "license-bundle.zip",
    ]:
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
