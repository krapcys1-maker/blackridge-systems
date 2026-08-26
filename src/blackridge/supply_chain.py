"""Independent legal, SBOM, vulnerability, posture, and provenance probes."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tomllib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Literal
from urllib.parse import quote
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field

from blackridge.depsdev import DepsDevClient, PackageSystem
from blackridge.errors import BlackridgeError
from blackridge.evidence import ProbeEvidence
from blackridge.quality import OpenSSFScorecardClient
from blackridge.sandbox import inspect_local_image

PYPI_INTEGRITY_DOCS = "https://docs.pypi.org/api/integrity/"
SYFT_SOURCE = "https://github.com/anchore/syft/tree/v1.51.0"
OSV_SCANNER_SOURCE = "https://github.com/google/osv-scanner/tree/v2.5.1"


class SupplyChainExperiment(BaseModel):
    """One exact repository/package release and pinned inspection tool images."""

    schema_version: Literal["1"] = "1"
    name: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    description: str = Field(min_length=20)
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    package_system: PackageSystem
    package_name: str = Field(min_length=1)
    package_version: str = Field(min_length=1)
    syft_image: str = Field(pattern=r"^anchore/syft@sha256:[a-f0-9]{64}$")
    osv_scanner_image: str = Field(
        pattern=r"^ghcr\.io/google/osv-scanner@sha256:[a-f0-9]{64}$"
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(
    argv: list[str],
    *,
    cwd: Path | None = None,
    accepted_exit_codes: set[int] | None = None,
) -> dict[str, object]:
    started = perf_counter()
    completed = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
    accepted = accepted_exit_codes or {0}
    observation = {
        "argv": argv,
        "duration_seconds": round(perf_counter() - started, 3),
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
    if completed.returncode not in accepted:
        raise BlackridgeError(
            f"command failed with exit {completed.returncode}: {argv[0]} "
            f"{completed.stderr.strip()}"
        )
    return observation


def _json_command(argv: list[str]) -> tuple[dict[str, object], dict[str, object]]:
    command = _run(argv)
    try:
        data = json.loads(str(command["stdout"]))
    except json.JSONDecodeError as exc:
        raise BlackridgeError(f"command returned invalid JSON: {' '.join(argv[:3])}") from exc
    if not isinstance(data, dict):
        raise BlackridgeError(f"command returned non-object JSON: {' '.join(argv[:3])}")
    return data, command


def _http_observation(
    url: str, *, headers: dict[str, str] | None = None
) -> dict[str, object]:
    try:
        response = httpx.get(url, headers=headers, timeout=30, follow_redirects=True)
        try:
            data: object = response.json()
        except ValueError:
            data = None
        return {
            "url": url,
            "status_code": response.status_code,
            "content_type": response.headers.get("content-type"),
            "data": data,
            "error": None,
        }
    except httpx.HTTPError as exc:
        return {
            "url": url,
            "status_code": None,
            "content_type": None,
            "data": None,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _ensure_exact_checkout(
    repository: str, commit: str, source_dir: Path
) -> tuple[list[dict[str, object]], str]:
    source_dir.mkdir(parents=True, exist_ok=True)
    commands: list[dict[str, object]] = []
    if not (source_dir / ".git").is_dir():
        commands.append(_run(["git", "init", str(source_dir)]))
        commands.append(
            _run(
                [
                    "git",
                    "-C",
                    str(source_dir),
                    "remote",
                    "add",
                    "origin",
                    f"https://github.com/{repository}.git",
                ]
            )
        )
    remote = _run(["git", "-C", str(source_dir), "remote", "get-url", "origin"])
    commands.append(remote)
    expected_remote = f"https://github.com/{repository}.git"
    if str(remote["stdout"]).strip().removesuffix(".git") != expected_remote.removesuffix(".git"):
        raise BlackridgeError("existing supply-chain source directory has a different origin")
    commands.append(
        _run(
            [
                "git",
                "-C",
                str(source_dir),
                "fetch",
                "--depth",
                "1",
                "origin",
                commit,
            ]
        )
    )
    commands.append(
        _run(["git", "-C", str(source_dir), "checkout", "--detach", "FETCH_HEAD"])
    )
    identity = _run(["git", "-C", str(source_dir), "rev-parse", "HEAD"])
    commands.append(identity)
    observed = str(identity["stdout"]).strip()
    if observed != commit:
        raise BlackridgeError(
            f"checkout identity mismatch: requested {commit}, observed {observed}"
        )
    return commands, observed


def _license_summary(spdx: dict[str, object]) -> dict[str, object]:
    packages = [item for item in spdx.get("packages") or [] if isinstance(item, dict)]
    no_assertion = [
        {
            "name": package.get("name"),
            "version": package.get("versionInfo"),
        }
        for package in packages
        if package.get("licenseDeclared") in {None, "NOASSERTION", "NONE"}
    ]
    return {
        "package_count": len(packages),
        "without_declared_license_count": len(no_assertion),
        "all_license_fields_unknown": len(no_assertion) == len(packages),
        "without_declared_license": no_assertion,
    }


def _vulnerability_summary(osv: dict[str, object]) -> dict[str, object]:
    results = [item for item in osv.get("results") or [] if isinstance(item, dict)]
    packages: list[dict[str, object]] = []
    for result in results:
        packages.extend(
            item for item in result.get("packages") or [] if isinstance(item, dict)
        )
    vulnerable: list[dict[str, object]] = []
    primary_ids: set[str] = set()
    severities: list[float] = []
    for item in packages:
        vulnerabilities = [
            value for value in item.get("vulnerabilities") or [] if isinstance(value, dict)
        ]
        if not vulnerabilities:
            continue
        groups = [value for value in item.get("groups") or [] if isinstance(value, dict)]
        group_ids: list[str] = []
        group_severities: list[float] = []
        for group in groups:
            group_ids.extend(str(value) for value in group.get("ids") or [])
            try:
                severity = float(group.get("max_severity"))
            except (TypeError, ValueError):
                continue
            group_severities.append(severity)
            severities.append(severity)
        for vulnerability in vulnerabilities:
            identifier = vulnerability.get("id")
            if identifier:
                primary_ids.add(str(identifier))
        vulnerable.append(
            {
                "package": item.get("package"),
                "group_count": len(groups),
                "group_ids": group_ids,
                "max_severity": max(group_severities) if group_severities else None,
            }
        )
    return {
        "scanned_package_count": len(packages),
        "vulnerable_package_entry_count": len(vulnerable),
        "unique_primary_advisory_count": len(primary_ids),
        "maximum_reported_severity": max(severities) if severities else None,
        "vulnerable_packages": vulnerable,
        "scope_warning": (
            "The SBOM was generated from the complete source tree. Findings are not yet "
            "classified by optional, development, or production reachability."
        ),
    }


class SupplyChainProbe:
    """Compose upstream scanners while retaining their observations independently."""

    def _direct_dependency_licenses(
        self, system: PackageSystem, direct_packages: list[dict[str, object]]
    ) -> list[dict[str, object]]:
        def inspect(package: dict[str, object]) -> dict[str, object]:
            name = str(package.get("name") or "")
            package_version = str(package.get("version") or "")
            url = (
                f"{DepsDevClient.base_url}/systems/{system.value}/packages/"
                f"{quote(name, safe='')}/versions/{quote(package_version, safe='')}"
            )
            response = _http_observation(url)
            data = response.get("data") if isinstance(response.get("data"), dict) else {}
            return {
                "name": name,
                "version": package_version,
                "status_code": response["status_code"],
                "licenses": data.get("licenses") if data else None,
                "advisories": [
                    item.get("id")
                    for item in data.get("advisoryKeys") or []
                    if isinstance(item, dict)
                ],
                "source": url,
                "error": response["error"],
            }

        with ThreadPoolExecutor(max_workers=8) as pool:
            return list(pool.map(inspect, direct_packages))

    def probe(
        self,
        experiment: SupplyChainExperiment,
        *,
        work_root: Path,
        artifact_dir: Path,
    ) -> ProbeEvidence:
        started = perf_counter()
        work_root = work_root.resolve()
        artifact_dir = artifact_dir.resolve()
        source_dir = work_root / "source"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        checkout_commands, observed_commit = _ensure_exact_checkout(
            experiment.repository, experiment.commit, source_dir
        )

        syft_image = inspect_local_image(experiment.syft_image)
        osv_image = inspect_local_image(experiment.osv_scanner_image)
        spdx_path = artifact_dir / f"{experiment.name}.spdx.json"
        cdx_path = artifact_dir / f"{experiment.name}.cdx.json"
        osv_path = artifact_dir / f"{experiment.name}.osv.json"
        syft_command = _run(
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
                "-e",
                "SYFT_CHECK_FOR_APP_UPDATE=false",
                "-v",
                f"{source_dir}:/src:ro",
                "-v",
                f"{artifact_dir}:/out",
                str(syft_image["resolved_id"]),
                "dir:/src",
                "--source-name",
                f"{experiment.repository}@{experiment.commit}",
                "-o",
                f"spdx-json=/out/{spdx_path.name}",
                "-o",
                f"cyclonedx-json=/out/{cdx_path.name}",
            ]
        )
        osv_command = _run(
            [
                "docker",
                "run",
                "--rm",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "-v",
                f"{artifact_dir}:/out",
                str(osv_image["resolved_id"]),
                "scan",
                "source",
                "-L",
                f"/out/{cdx_path.name}",
                "--format",
                "json",
                "--all-packages",
                "--all-vulns",
                "--output-file",
                f"/out/{osv_path.name}",
            ],
            accepted_exit_codes={0, 1},
        )
        try:
            spdx = json.loads(spdx_path.read_text(encoding="utf-8"))
            cdx = json.loads(cdx_path.read_text(encoding="utf-8"))
            osv = json.loads(osv_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BlackridgeError(f"cannot parse generated supply-chain artifact: {exc}") from exc

        repo_license, license_command = _json_command(
            [
                "gh",
                "api",
                f"repos/{experiment.repository}/license?ref={experiment.commit}",
            ]
        )
        commit_data, commit_command = _json_command(
            ["gh", "api", f"repos/{experiment.repository}/commits/{experiment.commit}"]
        )
        scorecard = OpenSSFScorecardClient().inspect(experiment.repository)
        package_probe = DepsDevClient().probe_package(
            experiment.package_system,
            experiment.package_name,
            version=experiment.package_version,
        )
        graph = package_probe.observations["dependency_graph"]
        direct_packages = (
            graph.get("direct_packages")
            if isinstance(graph, dict) and isinstance(graph.get("direct_packages"), list)
            else []
        )
        dependency_licenses = self._direct_dependency_licenses(
            experiment.package_system, direct_packages
        )

        pypi_url = (
            f"https://pypi.org/pypi/{quote(experiment.package_name, safe='')}/"
            f"{quote(experiment.package_version, safe='')}/json"
        )
        pypi = _http_observation(pypi_url)
        pypi_data = pypi["data"] if isinstance(pypi.get("data"), dict) else {}
        distribution_files: list[dict[str, object]] = []
        provenance: list[dict[str, object]] = []
        for item in pypi_data.get("urls") or []:
            if not isinstance(item, dict):
                continue
            filename = str(item.get("filename") or "")
            distribution_files.append(
                {
                    "filename": filename,
                    "packagetype": item.get("packagetype"),
                    "size": item.get("size"),
                    "sha256": (item.get("digests") or {}).get("sha256"),
                    "upload_time": item.get("upload_time_iso_8601"),
                }
            )
            provenance_url = (
                f"https://pypi.org/integrity/{quote(experiment.package_name, safe='')}/"
                f"{quote(experiment.package_version, safe='')}/"
                f"{quote(filename, safe='')}/provenance"
            )
            observation = _http_observation(
                provenance_url,
                headers={"Accept": "application/vnd.pypi.integrity.v1+json"},
            )
            data = observation["data"] if isinstance(observation.get("data"), dict) else {}
            provenance.append(
                {
                    "filename": filename,
                    "status_code": observation["status_code"],
                    "available": observation["status_code"] == 200,
                    "message": data.get("message"),
                    "attestation_bundle_count": len(data.get("attestation_bundles") or []),
                    "source": provenance_url,
                    "error": observation["error"],
                }
            )

        pyproject_path = source_dir / "pyproject.toml"
        pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
        project_metadata = pyproject.get("project")
        if not isinstance(project_metadata, dict):
            project_metadata = {}
        repo_license_path = repo_license.get("path")
        if not isinstance(repo_license_path, str) or not repo_license_path:
            raise BlackridgeError("GitHub license response does not identify a repository path")
        local_license_path = (source_dir / repo_license_path).resolve()
        if not local_license_path.is_relative_to(source_dir):
            raise BlackridgeError("GitHub license path resolves outside the source checkout")
        license_summary = _license_summary(spdx)
        vulnerability_summary = _vulnerability_summary(osv)
        nonstandard_dependencies = [
            item
            for item in dependency_licenses
            if not item.get("licenses")
            or "non-standard" in (item.get("licenses") or [])
            or any("GPL" in value for value in (item.get("licenses") or []))
        ]
        missing_provenance = [item["filename"] for item in provenance if not item["available"]]
        pypi_metadata_available = pypi["status_code"] == 200
        if not pypi_metadata_available:
            provenance_status = "unavailable"
        elif not distribution_files:
            provenance_status = "no-distribution-files"
        elif missing_provenance:
            provenance_status = "missing"
        else:
            provenance_status = "available"
        warnings: list[str] = []
        if scorecard.status != "available":
            warnings.append("OpenSSF Scorecard is unavailable; security posture remains unknown.")
        if license_summary["without_declared_license_count"]:
            warnings.append("The generated SBOM contains packages without declared license data.")
        if nonstandard_dependencies:
            warnings.append(
                "Direct dependency licenses include unknown, non-standard, or GPL-family results."
            )
        if not pypi_metadata_available:
            warnings.append("PyPI release metadata is unavailable; provenance remains unknown.")
        elif not distribution_files:
            warnings.append("PyPI release metadata contains no distribution files to verify.")
        elif missing_provenance:
            warnings.append("PyPI provenance is unavailable for at least one distribution file.")
        if vulnerability_summary["vulnerable_package_entry_count"]:
            warnings.append(
                "OSV-Scanner reported known vulnerabilities in the complete lock scope."
            )

        source_urls = [
            f"https://github.com/{experiment.repository}/commit/{experiment.commit}",
            pypi_url,
            *package_probe.sources,
            PYPI_INTEGRITY_DOCS,
            SYFT_SOURCE,
            OSV_SCANNER_SOURCE,
        ]
        return ProbeEvidence(
            probe_id=uuid4().hex,
            observed_at=datetime.now(UTC),
            provider="github+deps.dev+scorecard+syft+osv-scanner+pypi-integrity",
            subject=(
                f"{experiment.repository}@{experiment.commit}::"
                f"{experiment.package_system.value}:{experiment.package_name}"
                f"@{experiment.package_version}"
            ),
            request=experiment.model_dump(),
            observations={
                "probe_completed": True,
                "duration_seconds": round(perf_counter() - started, 3),
                "source": {
                    "requested_commit": experiment.commit,
                    "observed_commit": observed_commit,
                    "checkout_commands": checkout_commands,
                },
                "repository_license": {
                    "spdx_id": (repo_license.get("license") or {}).get("spdx_id"),
                    "name": (repo_license.get("license") or {}).get("name"),
                    "path": repo_license.get("path"),
                    "git_blob_sha": repo_license.get("sha"),
                    "html_url": repo_license.get("html_url"),
                    "local_sha256": _sha256(local_license_path),
                    "pyproject_license": project_metadata.get("license"),
                    "github_command": license_command,
                },
                "dependency_licenses": {
                    "direct_dependency_count": len(dependency_licenses),
                    "packages": dependency_licenses,
                    "concern_count": len(nonstandard_dependencies),
                    "concerns": nonstandard_dependencies,
                    "sbom_license_coverage": license_summary,
                },
                "security_posture": {
                    "scorecard": {
                        "status": scorecard.status,
                        "score": scorecard.score,
                        "detail": scorecard.detail,
                        "scope": "repository-level current snapshot; not commit-scoped",
                    }
                },
                "known_vulnerabilities": {
                    "osv_scanner_exit_code": osv_command["exit_code"],
                    **vulnerability_summary,
                },
                "sbom": {
                    "syft_image": syft_image,
                    "syft_command": syft_command,
                    "spdx": {
                        "filename": spdx_path.name,
                        "sha256": _sha256(spdx_path),
                        "size": spdx_path.stat().st_size,
                        "package_count": len(spdx.get("packages") or []),
                        "relationship_count": len(spdx.get("relationships") or []),
                        "document_name": spdx.get("name"),
                    },
                    "cyclonedx": {
                        "filename": cdx_path.name,
                        "sha256": _sha256(cdx_path),
                        "size": cdx_path.stat().st_size,
                        "component_count": len(cdx.get("components") or []),
                        "dependency_count": len(cdx.get("dependencies") or []),
                    },
                },
                "vulnerability_artifact": {
                    "osv_scanner_image": osv_image,
                    "osv_scanner_command": osv_command,
                    "filename": osv_path.name,
                    "sha256": _sha256(osv_path),
                    "size": osv_path.stat().st_size,
                },
                "release_provenance": {
                    "status": provenance_status,
                    "pypi_release_metadata": {
                        "url": pypi_url,
                        "status_code": pypi["status_code"],
                        "error": pypi["error"],
                    },
                    "distribution_files": distribution_files,
                    "pypi_integrity": provenance,
                    "missing_files": missing_provenance,
                    "commit_verification": {
                        "verified": (commit_data.get("commit") or {})
                        .get("verification", {})
                        .get("verified"),
                        "reason": (commit_data.get("commit") or {})
                        .get("verification", {})
                        .get("reason"),
                        "html_url": commit_data.get("html_url"),
                    },
                    "github_commit_command": commit_command,
                },
                "package_evidence": package_probe.observations,
            },
            sources=list(dict.fromkeys(source_urls)),
            warnings=warnings,
        )
