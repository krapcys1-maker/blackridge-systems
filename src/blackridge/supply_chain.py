"""Independent legal, SBOM, vulnerability, posture, and provenance probes."""

from __future__ import annotations

import ast
import configparser
import hashlib
import importlib.metadata
import json
import tomllib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Literal
from urllib.parse import quote
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field

from blackridge.depsdev import DepsDevClient, PackageSystem
from blackridge.errors import BlackridgeError
from blackridge.evidence import ProbeEvidence
from blackridge.git_integrity import inspect_pristine_checkout
from blackridge.process_boundary import resolve_executable, run_bounded
from blackridge.quality import OpenSSFScorecardClient
from blackridge.sandbox import inspect_local_image

PYPI_INTEGRITY_DOCS = "https://docs.pypi.org/api/integrity/"
SYFT_SOURCE = "https://github.com/anchore/syft/tree/v1.51.0"
OSV_SCANNER_SOURCE = "https://github.com/google/osv-scanner/tree/v2.5.1"
PYPI_ATTESTATIONS_VERSION = "0.0.30"
EXACT_LOCK_FILENAMES = ("Pipfile.lock", "pdm.lock", "poetry.lock", "requirements.lock", "uv.lock")


class SupplyChainExperiment(BaseModel):
    """One exact repository/package release and pinned inspection tool images."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    name: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    description: str = Field(min_length=20)
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    package_system: PackageSystem
    package_name: str = Field(min_length=1)
    package_version: str = Field(min_length=1)
    syft_image: str = Field(pattern=r"^anchore/syft@sha256:[a-f0-9]{64}$")
    osv_scanner_image: str = Field(pattern=r"^ghcr\.io/google/osv-scanner@sha256:[a-f0-9]{64}$")

    def model_post_init(self, _context: object) -> None:
        if self.package_system != PackageSystem.PYPI:
            raise ValueError("supply-chain probe v1 currently supports only pypi packages")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_json(value: object) -> str:
    """Serialize JSON data with mapping and collection ordering removed."""

    normalized: object
    if isinstance(value, dict):
        normalized = {key: json.loads(_canonical_json(item)) for key, item in value.items()}
    elif isinstance(value, list):
        items = [json.loads(_canonical_json(item)) for item in value]
        normalized = sorted(
            items,
            key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")),
        )
    else:
        normalized = value
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _inventory_sha256(document: dict[str, object], sections: tuple[str, ...]) -> str:
    """Hash the stable package graph while excluding generator timestamps and UUIDs."""

    inventory = {
        section: _object_list(
            document.get(section),
            f"SBOM {section}",
            required=section in {"packages", "components"},
        )
        for section in sections
    }
    return hashlib.sha256(_canonical_json(inventory).encode()).hexdigest()


def _object_list(value: object, context: str, *, required: bool = True) -> list[dict[str, Any]]:
    if value is None and not required:
        return []
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise BlackridgeError(f"{context} must be a list of JSON objects")
    return value


def _optional_object(value: object, context: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise BlackridgeError(f"{context} must be a JSON object")
    return value


def _packaging_metadata(source_dir: Path) -> tuple[dict[str, Any], str | None]:
    """Read packaging metadata without importing or executing untrusted project code."""

    pyproject_path = source_dir / "pyproject.toml"
    if pyproject_path.is_file():
        try:
            pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise BlackridgeError(f"cannot parse pyproject.toml: {exc}") from exc
        project = pyproject.get("project")
        return (project if isinstance(project, dict) else {}), "pyproject.toml"

    setup_cfg_path = source_dir / "setup.cfg"
    if setup_cfg_path.is_file():
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read(setup_cfg_path, encoding="utf-8")
        except (OSError, configparser.Error) as exc:
            raise BlackridgeError(f"cannot parse setup.cfg: {exc}") from exc
        metadata: dict[str, Any] = {}
        if parser.has_option("metadata", "license"):
            metadata["license"] = parser.get("metadata", "license")
        if parser.has_option("options", "install_requires"):
            metadata["dependencies"] = [
                value.strip()
                for value in parser.get("options", "install_requires").splitlines()
                if value.strip()
            ]
        return metadata, "setup.cfg"

    setup_py_path = source_dir / "setup.py"
    if setup_py_path.is_file():
        try:
            tree = ast.parse(setup_py_path.read_text(encoding="utf-8"), filename="setup.py")
        except (OSError, SyntaxError) as exc:
            raise BlackridgeError(f"cannot statically parse setup.py: {exc}") from exc
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and (
                (isinstance(node.func, ast.Name) and node.func.id == "setup")
                or (isinstance(node.func, ast.Attribute) and node.func.attr == "setup")
            )
        ]
        metadata = {}
        if calls:
            for keyword in calls[0].keywords:
                target = "dependencies" if keyword.arg == "install_requires" else keyword.arg
                if target not in {"name", "version", "license", "dependencies"}:
                    continue
                try:
                    metadata[target] = ast.literal_eval(keyword.value)
                except (TypeError, ValueError):
                    continue
        return metadata, "setup.py (static AST)"

    return {}, None


def _dependency_input_summary(source_dir: Path) -> dict[str, object]:
    manifests = {
        name
        for name in ("Pipfile", "pyproject.toml", "setup.cfg", "setup.py")
        if (source_dir / name).is_file()
    }
    manifests.update(path.name for path in source_dir.glob("requirements*.txt") if path.is_file())
    lockfiles = [name for name in EXACT_LOCK_FILENAMES if (source_dir / name).is_file()]
    return {
        "manifest_files": sorted(manifests),
        "lockfiles": lockfiles,
        "exact_lock_present": bool(lockfiles),
    }


def _run(
    argv: list[str],
    *,
    cwd: Path | None = None,
    accepted_exit_codes: set[int] | None = None,
) -> dict[str, object]:
    completed = run_bounded(argv, cwd=cwd)
    accepted = accepted_exit_codes or {0}
    observation = {
        "argv": argv,
        "duration_seconds": completed.duration_seconds,
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
    if completed.returncode not in accepted:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no output"
        raise BlackridgeError(
            f"command failed with exit {completed.returncode}: {argv[0]} {detail[:1000]}"
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


def _http_observation(url: str, *, headers: dict[str, str] | None = None) -> dict[str, object]:
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


def _inspect_checkout(
    source_dir: Path,
    *,
    expected_commit: str | None = None,
    expected_tree: str | None = None,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    return inspect_pristine_checkout(
        source_dir,
        expected_commit=expected_commit,
        expected_tree=expected_tree,
        context="supply-chain source checkout",
    )


def _ensure_exact_checkout(
    repository: str, commit: str, source_dir: Path
) -> tuple[list[dict[str, object]], dict[str, object]]:
    if source_dir.exists() and not (source_dir / ".git").is_dir() and any(source_dir.iterdir()):
        raise BlackridgeError("supply-chain source directory is non-empty and is not Git")
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
    else:
        preflight_commands, _ = _inspect_checkout(source_dir)
        commands.extend(preflight_commands)
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
    commands.append(_run(["git", "-C", str(source_dir), "checkout", "--detach", "FETCH_HEAD"]))
    identity_commands, state = _inspect_checkout(
        source_dir,
        expected_commit=commit,
    )
    commands.extend(identity_commands)
    return commands, state


def _pypi_attestation_verifier() -> tuple[str | None, str | None]:
    executable = resolve_executable("pypi-attestations")
    try:
        version = importlib.metadata.version("pypi-attestations")
    except importlib.metadata.PackageNotFoundError:
        version = None
    if executable is None or version != PYPI_ATTESTATIONS_VERSION:
        return None, version
    return executable, version


def _license_summary(spdx: dict[str, object]) -> dict[str, object]:
    packages = _object_list(spdx.get("packages"), "SPDX packages")
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
    results = _object_list(osv.get("results"), "OSV results")
    packages: list[dict[str, object]] = []
    for result in results:
        packages.extend(_object_list(result.get("packages"), "OSV result packages"))
    vulnerable: list[dict[str, object]] = []
    primary_ids: set[str] = set()
    severities: list[float] = []
    for item in packages:
        vulnerabilities = _object_list(
            item.get("vulnerabilities"), "OSV package vulnerabilities", required=False
        )
        if not vulnerabilities:
            continue
        groups = _object_list(item.get("groups"), "OSV package groups", required=False)
        group_ids: list[str] = []
        group_severities: list[float] = []
        for group in groups:
            ids = group.get("ids")
            if not isinstance(ids, list):
                raise BlackridgeError("OSV group ids must be a list")
            group_ids.extend(str(value) for value in ids)
            severity_value = group.get("max_severity")
            if not isinstance(severity_value, (str, int, float)):
                continue
            try:
                severity = float(severity_value)
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
            data = _optional_object(response.get("data"), "deps.dev dependency response")
            licenses = data.get("licenses")
            if licenses is not None and not isinstance(licenses, list):
                raise BlackridgeError("deps.dev dependency licenses must be a list")
            advisories = data.get("advisoryKeys")
            if advisories is not None and not isinstance(advisories, list):
                raise BlackridgeError("deps.dev dependency advisoryKeys must be a list")
            return {
                "name": name,
                "version": package_version,
                "status_code": response["status_code"],
                "licenses": licenses,
                "advisories": [
                    item.get("id") for item in advisories or [] if isinstance(item, dict)
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
        checkout_commands, checkout_before = _ensure_exact_checkout(
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
        if not all(isinstance(value, dict) for value in (spdx, cdx, osv)):
            raise BlackridgeError("generated supply-chain artifacts must contain JSON objects")

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
        graph = _optional_object(
            package_probe.observations.get("dependency_graph"),
            "deps.dev dependency graph",
        )
        direct_packages = _object_list(
            graph.get("direct_packages"),
            "deps.dev direct packages",
        )
        dependency_licenses = self._direct_dependency_licenses(
            experiment.package_system, direct_packages
        )

        pypi_url = (
            f"https://pypi.org/pypi/{quote(experiment.package_name, safe='')}/"
            f"{quote(experiment.package_version, safe='')}/json"
        )
        pypi = _http_observation(pypi_url)
        pypi_data = _optional_object(pypi.get("data"), "PyPI release response")
        verifier_executable, verifier_version = _pypi_attestation_verifier()
        distribution_files: list[dict[str, object]] = []
        provenance: list[dict[str, object]] = []
        pypi_urls = pypi_data.get("urls")
        if pypi_urls is not None and not isinstance(pypi_urls, list):
            raise BlackridgeError("PyPI release urls must be a list")
        for item in pypi_urls or []:
            if not isinstance(item, dict):
                raise BlackridgeError("PyPI release url entries must be objects")
            filename = str(item.get("filename") or "")
            digests = _optional_object(item.get("digests"), "PyPI distribution digests")
            distribution_files.append(
                {
                    "filename": filename,
                    "packagetype": item.get("packagetype"),
                    "size": item.get("size"),
                    "sha256": digests.get("sha256"),
                    "upload_time": item.get("upload_time_iso_8601"),
                    "url": item.get("url"),
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
            data = _optional_object(observation.get("data"), "PyPI provenance response")
            bundles = data.get("attestation_bundles")
            if bundles is not None and not isinstance(bundles, list):
                raise BlackridgeError("PyPI provenance attestation_bundles must be a list")
            artifact_url = item.get("url")
            verification_command = None
            verified = False
            if verifier_executable is not None and isinstance(artifact_url, str):
                verification_command = _run(
                    [
                        verifier_executable,
                        "verify",
                        "pypi",
                        "--repository",
                        f"https://github.com/{experiment.repository}",
                        artifact_url,
                    ],
                    accepted_exit_codes={0, 1, 2},
                )
                verified = verification_command["exit_code"] == 0
            provenance.append(
                {
                    "filename": filename,
                    "status_code": observation["status_code"],
                    "available": observation["status_code"] == 200,
                    "message": data.get("message"),
                    "attestation_bundle_count": len(bundles or []),
                    "cryptographically_verified": verified,
                    "repository_identity_expected": experiment.repository,
                    "source_commit_bound": False,
                    "verification_command": verification_command,
                    "source": provenance_url,
                    "error": observation["error"],
                }
            )

        project_metadata, packaging_metadata_source = _packaging_metadata(source_dir)
        dependency_inputs = _dependency_input_summary(source_dir)
        repo_license_path = repo_license.get("path")
        if not isinstance(repo_license_path, str) or not repo_license_path:
            raise BlackridgeError("GitHub license response does not identify a repository path")
        local_license_path = (source_dir / repo_license_path).resolve()
        if not local_license_path.is_relative_to(source_dir):
            raise BlackridgeError("GitHub license path resolves outside the source checkout")
        repository_license = _optional_object(
            repo_license.get("license"), "GitHub repository license"
        )
        commit = _optional_object(commit_data.get("commit"), "GitHub commit")
        verification = _optional_object(commit.get("verification"), "GitHub commit verification")
        license_summary = _license_summary(spdx)
        vulnerability_summary = _vulnerability_summary(osv)
        nonstandard_dependencies: list[dict[str, object]] = []
        for item in dependency_licenses:
            licenses = item.get("licenses")
            if not isinstance(licenses, list):
                nonstandard_dependencies.append(item)
                continue
            if (
                not licenses
                or "non-standard" in licenses
                or any("GPL" in str(value) for value in licenses)
            ):
                nonstandard_dependencies.append(item)
        post_checkout_commands, checkout_after = _inspect_checkout(
            source_dir,
            expected_commit=experiment.commit,
            expected_tree=str(checkout_before["tree"]),
        )
        missing_provenance = [item["filename"] for item in provenance if not item["available"]]
        unverified_provenance = [
            item["filename"] for item in provenance if not item["cryptographically_verified"]
        ]
        pypi_metadata_available = pypi["status_code"] == 200
        if not pypi_metadata_available:
            provenance_status = "unavailable"
        elif not distribution_files:
            provenance_status = "no-distribution-files"
        elif missing_provenance:
            provenance_status = "missing"
        elif unverified_provenance:
            provenance_status = "available-unverified"
        else:
            provenance_status = "verified-artifact-and-repository"
        warnings: list[str] = []
        if scorecard.status != "available":
            warnings.append("OpenSSF Scorecard is unavailable; security posture remains unknown.")
        if license_summary["without_declared_license_count"]:
            warnings.append("The generated SBOM contains packages without declared license data.")
        if nonstandard_dependencies:
            warnings.append(
                "Direct dependency licenses include unknown, non-standard, or GPL-family results."
            )
        if not dependency_inputs["exact_lock_present"]:
            warnings.append(
                "No recognized exact dependency lockfile is present; resolved-version evidence "
                "is not a reproducible runtime closure."
            )
        cdx_dependencies = _object_list(
            cdx.get("dependencies"), "CycloneDX dependencies", required=False
        )
        if not cdx_dependencies:
            warnings.append(
                "The CycloneDX SBOM contains no dependency edges; reachability remains unknown."
            )
        if not pypi_metadata_available:
            warnings.append("PyPI release metadata is unavailable; provenance remains unknown.")
        elif not distribution_files:
            warnings.append("PyPI release metadata contains no distribution files to verify.")
        elif missing_provenance:
            warnings.append("PyPI provenance is unavailable for at least one distribution file.")
        elif unverified_provenance:
            warnings.append(
                "PyPI provenance exists but was not cryptographically verified for every file."
            )
        if verifier_executable is None:
            warnings.append(
                "Install the supply-chain extra with the pinned pypi-attestations verifier."
            )
        if provenance:
            warnings.append(
                "PyPI provenance binds artifacts and repository identity, not the requested "
                "source commit."
            )
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
                    "observed_commit": checkout_before["commit"],
                    "tree": checkout_before["tree"],
                    "checkout_before_scanners": checkout_before,
                    "checkout_after_scanners": checkout_after,
                    "checkout_commands": checkout_commands,
                    "post_scanner_checkout_commands": post_checkout_commands,
                },
                "repository_license": {
                    "spdx_id": repository_license.get("spdx_id"),
                    "name": repository_license.get("name"),
                    "path": repo_license.get("path"),
                    "git_blob_sha": repo_license.get("sha"),
                    "html_url": repo_license.get("html_url"),
                    "local_sha256": _sha256(local_license_path),
                    "pyproject_license": project_metadata.get("license"),
                    "packaging_metadata_license": project_metadata.get("license"),
                    "packaging_metadata_source": packaging_metadata_source,
                    "github_command": license_command,
                },
                "dependency_licenses": {
                    "direct_dependency_count": len(dependency_licenses),
                    "packages": dependency_licenses,
                    "concern_count": len(nonstandard_dependencies),
                    "concerns": nonstandard_dependencies,
                    "dependency_inputs": dependency_inputs,
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
                        "inventory_sha256": _inventory_sha256(spdx, ("packages", "relationships")),
                        "size": spdx_path.stat().st_size,
                        "package_count": len(_object_list(spdx.get("packages"), "SPDX packages")),
                        "relationship_count": len(
                            _object_list(
                                spdx.get("relationships"),
                                "SPDX relationships",
                                required=False,
                            )
                        ),
                        "document_name": spdx.get("name"),
                    },
                    "cyclonedx": {
                        "filename": cdx_path.name,
                        "sha256": _sha256(cdx_path),
                        "inventory_sha256": _inventory_sha256(cdx, ("components", "dependencies")),
                        "size": cdx_path.stat().st_size,
                        "component_count": len(
                            _object_list(cdx.get("components"), "CycloneDX components")
                        ),
                        "dependency_count": len(cdx_dependencies),
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
                    "unverified_files": unverified_provenance,
                    "verifier": {
                        "name": "pypi-attestations",
                        "required_version": PYPI_ATTESTATIONS_VERSION,
                        "observed_version": verifier_version,
                        "available": verifier_executable is not None,
                    },
                    "commit_verification": {
                        "verified": verification.get("verified"),
                        "reason": verification.get("reason"),
                        "html_url": commit_data.get("html_url"),
                    },
                    "github_commit_command": commit_command,
                },
                "package_evidence": package_probe.observations,
            },
            sources=list(dict.fromkeys(source_urls)),
            warnings=warnings,
        )
