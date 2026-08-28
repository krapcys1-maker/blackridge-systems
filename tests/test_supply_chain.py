from __future__ import annotations

import importlib.metadata
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from blackridge.errors import BlackridgeError
from blackridge.evidence import ProbeEvidence
from blackridge.process_boundary import BoundedProcessResult
from blackridge.quality import ScorecardObservation
from blackridge.supply_chain import (
    PYPI_ATTESTATIONS_VERSION,
    SupplyChainExperiment,
    SupplyChainProbe,
    _dependency_input_summary,
    _http_observation,
    _inspect_checkout,
    _inventory_sha256,
    _json_command,
    _license_summary,
    _object_list,
    _optional_object,
    _packaging_metadata,
    _pypi_attestation_verifier,
    _run,
    _vulnerability_summary,
)


def experiment_data() -> dict[str, object]:
    return {
        "name": "exact-release-supply-chain",
        "description": "Inspect one exact repository and package release independently.",
        "repository": "Future-House/paper-qa",
        "commit": "57e89f7223b0960d5ee5ea048c69e3c47e088572",
        "package_system": "pypi",
        "package_name": "paper-qa",
        "package_version": "2026.8.12",
        "syft_image": "anchore/syft@sha256:" + "a" * 64,
        "osv_scanner_image": "ghcr.io/google/osv-scanner@sha256:" + "b" * 64,
    }


def test_supply_chain_requires_exact_commit_and_image_digests() -> None:
    data = experiment_data()
    data["commit"] = "main"

    with pytest.raises(ValidationError):
        SupplyChainExperiment.model_validate(data)


def test_supply_chain_rejects_an_unsupported_package_ecosystem() -> None:
    data = experiment_data()
    data["package_system"] = "npm"

    with pytest.raises(ValidationError, match="only pypi"):
        SupplyChainExperiment.model_validate(data)


def test_checkout_inspection_rejects_tracked_and_ignored_residue(tmp_path) -> None:
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("clean\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "fixture"], cwd=tmp_path, check=True)

    _, clean = _inspect_checkout(tmp_path)
    assert clean["pristine"] is True

    tracked.write_text("dirty\n", encoding="utf-8")
    with pytest.raises(BlackridgeError, match="not pristine"):
        _inspect_checkout(tmp_path)
    tracked.write_text("clean\n", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("residue\n", encoding="utf-8")
    with pytest.raises(BlackridgeError, match="not pristine"):
        _inspect_checkout(tmp_path)


def test_sbom_unknown_licenses_remain_explicit() -> None:
    summary = _license_summary(
        {
            "packages": [
                {"name": "known", "versionInfo": "1", "licenseDeclared": "MIT"},
                {"name": "unknown", "versionInfo": "2", "licenseDeclared": "NOASSERTION"},
            ]
        }
    )

    assert summary["package_count"] == 2
    assert summary["without_declared_license_count"] == 1
    assert summary["all_license_fields_unknown"] is False


def test_packaging_metadata_statically_reads_legacy_setup_without_execution(tmp_path) -> None:
    (tmp_path / "setup.py").write_text(
        "from pathlib import Path\n"
        "Path('executed').write_text('unsafe')\n"
        "setup(name='fixture', version='1.0', license='MIT', "
        "install_requires=['httpx', 'tenacity'])\n",
        encoding="utf-8",
    )

    metadata, source = _packaging_metadata(tmp_path)

    assert source == "setup.py (static AST)"
    assert metadata == {
        "name": "fixture",
        "version": "1.0",
        "license": "MIT",
        "dependencies": ["httpx", "tenacity"],
    }
    assert not (tmp_path / "executed").exists()


def test_dependency_input_summary_distinguishes_manifest_from_exact_lock(tmp_path) -> None:
    (tmp_path / "setup.py").write_text("setup()\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("httpx\n", encoding="utf-8")

    unlocked = _dependency_input_summary(tmp_path)
    (tmp_path / "requirements.lock").write_text(
        "httpx==0.28.1 --hash=sha256:fixture\n", encoding="utf-8"
    )
    locked = _dependency_input_summary(tmp_path)

    assert unlocked == {
        "manifest_files": ["requirements.txt", "setup.py"],
        "lockfiles": [],
        "exact_lock_present": False,
    }
    assert locked["lockfiles"] == ["requirements.lock"]
    assert locked["exact_lock_present"] is True


def test_sbom_inventory_hash_ignores_volatile_metadata_and_collection_order() -> None:
    first = {
        "creationInfo": {"created": "2026-08-27T10:00:00Z"},
        "documentNamespace": "https://example.test/first",
        "packages": [{"name": "b", "licenses": ["MIT", "Apache-2.0"]}, {"name": "a"}],
        "relationships": [{"relatedSpdxElement": "b", "spdxElementId": "a"}],
    }
    second = {
        "creationInfo": {"created": "2026-08-27T10:01:00Z"},
        "documentNamespace": "https://example.test/second",
        "packages": [{"name": "a"}, {"licenses": ["Apache-2.0", "MIT"], "name": "b"}],
        "relationships": [{"spdxElementId": "a", "relatedSpdxElement": "b"}],
    }

    assert _inventory_sha256(first, ("packages", "relationships")) == _inventory_sha256(
        second, ("packages", "relationships")
    )


def test_sbom_inventory_hash_accepts_omitted_empty_edge_collection() -> None:
    without_edges = {"components": [{"name": "fixture"}]}
    with_edges = {"components": [{"name": "fixture"}], "dependencies": []}

    assert _inventory_sha256(without_edges, ("components", "dependencies")) == _inventory_sha256(
        with_edges, ("components", "dependencies")
    )


def test_vulnerability_summary_keeps_scope_and_severity_separate() -> None:
    summary = _vulnerability_summary(
        {
            "results": [
                {
                    "packages": [
                        {"package": {"name": "clean", "version": "1"}},
                        {
                            "package": {"name": "affected", "version": "2"},
                            "groups": [
                                {
                                    "ids": ["GHSA-example"],
                                    "max_severity": "9.1",
                                }
                            ],
                            "vulnerabilities": [{"id": "OSV-example"}],
                        },
                    ]
                }
            ]
        }
    )

    assert summary["scanned_package_count"] == 2
    assert summary["vulnerable_package_entry_count"] == 1
    assert summary["unique_primary_advisory_count"] == 1
    assert summary["maximum_reported_severity"] == 9.1
    assert "production reachability" in summary["scope_warning"]


def test_scanner_summaries_reject_wrong_json_shapes() -> None:
    with pytest.raises(BlackridgeError, match="SPDX packages"):
        _license_summary({"packages": "wrong-type"})
    with pytest.raises(BlackridgeError, match="OSV results"):
        _vulnerability_summary({"results": {}})


def _process(
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
    timed_out: bool = False,
    output_limit_exceeded: bool = False,
) -> BoundedProcessResult:
    return BoundedProcessResult(
        argv=("fixture",),
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=0.01,
        timed_out=timed_out,
        output_limit_exceeded=output_limit_exceeded,
        stdout_bytes_seen=len(stdout.encode()),
        stderr_bytes_seen=len(stderr.encode()),
    )


@pytest.mark.parametrize(
    ("process", "message"),
    [
        (_process(timed_out=True), "command timed out: scanner"),
        (_process(output_limit_exceeded=True), "command exceeded the output limit: scanner"),
        (_process(returncode=7, stderr="scanner failed"), "scanner failed"),
        (_process(returncode=8, stdout="stdout fallback"), "stdout fallback"),
        (_process(returncode=9), "no output"),
    ],
)
def test_supply_chain_command_boundary_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    process: BoundedProcessResult,
    message: str,
) -> None:
    monkeypatch.setattr("blackridge.supply_chain.run_bounded", lambda *_args, **_kwargs: process)

    with pytest.raises(BlackridgeError, match=message):
        _run(["scanner"])


def test_supply_chain_command_boundary_accepts_explicit_scanner_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "blackridge.supply_chain.run_bounded",
        lambda *_args, **_kwargs: _process(returncode=1, stdout="findings"),
    )

    observation = _run(["osv-scanner"], accepted_exit_codes={0, 1})

    assert observation["exit_code"] == 1
    assert observation["stdout"] == "findings"


def test_supply_chain_command_error_detail_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "blackridge.supply_chain.run_bounded",
        lambda *_args, **_kwargs: _process(returncode=1, stderr="x" * 2000),
    )

    with pytest.raises(BlackridgeError) as caught:
        _run(["scanner"])

    assert str(caught.value).endswith("x" * 1000)
    assert len(str(caught.value)) < 1100


@pytest.mark.parametrize("raw", ["not-json", "[]"])
def test_json_command_rejects_invalid_or_non_object_output(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setattr("blackridge.supply_chain._run", lambda _argv: {"stdout": raw})

    with pytest.raises(BlackridgeError, match=r"invalid JSON|non-object JSON"):
        _json_command(["gh", "api", "fixture"])


def test_json_shape_helpers_distinguish_optional_and_malformed_values() -> None:
    assert _object_list(None, "optional", required=False) == []
    assert _optional_object(None, "optional") == {}
    with pytest.raises(BlackridgeError, match="items must be a list"):
        _object_list([{}, "wrong"], "items")
    with pytest.raises(BlackridgeError, match="item must be a JSON object"):
        _optional_object([], "item")


def test_http_observation_retains_invalid_json_and_transport_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = httpx.Response(
        502,
        headers={"content-type": "text/plain"},
        text="upstream failure",
        request=httpx.Request("GET", "https://example.test/data"),
    )
    monkeypatch.setattr("blackridge.supply_chain.httpx.get", lambda *_args, **_kwargs: response)

    invalid = _http_observation("https://example.test/data")

    assert invalid["status_code"] == 502
    assert invalid["data"] is None
    assert invalid["error"] is None

    def fail(*_args: object, **_kwargs: object) -> object:
        raise httpx.ConnectError("offline")

    monkeypatch.setattr("blackridge.supply_chain.httpx.get", fail)
    failed = _http_observation("https://example.test/data")
    assert failed["status_code"] is None
    assert failed["data"] is None
    assert str(failed["error"]).startswith("ConnectError: offline")


def test_attestation_verifier_requires_both_executable_and_exact_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("blackridge.supply_chain.resolve_executable", lambda _name: "/tools/attest")
    monkeypatch.setattr(
        "blackridge.supply_chain.importlib.metadata.version", lambda _name: "0.0.29"
    )
    assert _pypi_attestation_verifier() == (None, "0.0.29")

    monkeypatch.setattr(
        "blackridge.supply_chain.importlib.metadata.version",
        lambda _name: PYPI_ATTESTATIONS_VERSION,
    )
    assert _pypi_attestation_verifier() == ("/tools/attest", PYPI_ATTESTATIONS_VERSION)

    def missing(_name: str) -> str:
        raise importlib.metadata.PackageNotFoundError

    monkeypatch.setattr("blackridge.supply_chain.importlib.metadata.version", missing)
    assert _pypi_attestation_verifier() == (None, None)


class _DepsDevFixture:
    base_url = "https://api.deps.dev/v3alpha"

    def probe_package(self, *_args: object, **_kwargs: object) -> ProbeEvidence:
        return ProbeEvidence(
            probe_id="d" * 32,
            observed_at=datetime(2026, 8, 27, tzinfo=UTC),
            provider="deps.dev-fixture",
            subject="pypi:fixture@1.0",
            request={},
            observations={
                "dependency_graph": {
                    "direct_packages": [
                        {"name": "safe-dependency", "version": "1"},
                        {"name": "gpl-dependency", "version": "2"},
                    ]
                }
            },
            sources=["https://deps.example.test/package"],
        )


class _ScorecardFixture:
    def inspect(self, _repository: str) -> ScorecardObservation:
        return ScorecardObservation(None, "not-found", "fixture has no score")


def test_supply_chain_probe_retains_missing_provenance_and_vulnerabilities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    experiment = SupplyChainExperiment.model_validate(experiment_data())
    work_root = tmp_path / "work"
    artifact_dir = tmp_path / "artifacts"
    source_dir = work_root / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "LICENSE").write_text("fixture license\n", encoding="utf-8")
    (source_dir / "pyproject.toml").write_text(
        '[project]\nname = "fixture"\nversion = "1.0"\nlicense = "Apache-2.0"\n',
        encoding="utf-8",
    )
    checkout = {
        "commit": experiment.commit,
        "tree": "1" * 40,
        "pristine": True,
    }
    monkeypatch.setattr(
        "blackridge.supply_chain._ensure_exact_checkout",
        lambda *_args, **_kwargs: ([{"exit_code": 0}], checkout),
    )
    monkeypatch.setattr(
        "blackridge.supply_chain._inspect_checkout",
        lambda *_args, **_kwargs: ([{"exit_code": 0}], checkout),
    )
    monkeypatch.setattr(
        "blackridge.supply_chain.inspect_local_image",
        lambda image: {"requested": image, "resolved_id": "sha256:" + "c" * 64},
    )

    def command(argv: list[str], **_kwargs: object) -> dict[str, object]:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        if "anchore/syft" not in " ".join(argv) and "dir:/src" not in argv:
            (artifact_dir / f"{experiment.name}.osv.json").write_text(
                json.dumps(
                    {
                        "results": [
                            {
                                "packages": [
                                    {
                                        "package": {"name": "affected", "version": "3"},
                                        "groups": [
                                            {"ids": ["GHSA-fixture"], "max_severity": "8.8"}
                                        ],
                                        "vulnerabilities": [{"id": "OSV-fixture"}],
                                    }
                                ]
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            return {"exit_code": 1, "argv": argv}
        (artifact_dir / f"{experiment.name}.spdx.json").write_text(
            json.dumps(
                {
                    "name": "fixture-sbom",
                    "packages": [
                        {
                            "name": "fixture",
                            "versionInfo": "1.0",
                            "licenseDeclared": "NOASSERTION",
                        }
                    ],
                    "relationships": [],
                }
            ),
            encoding="utf-8",
        )
        (artifact_dir / f"{experiment.name}.cdx.json").write_text(
            json.dumps({"components": [{}], "dependencies": []}), encoding="utf-8"
        )
        return {"exit_code": 0, "argv": argv}

    monkeypatch.setattr("blackridge.supply_chain._run", command)

    def json_command(argv: list[str]) -> tuple[dict[str, object], dict[str, object]]:
        if "license?ref=" in argv[-1]:
            return (
                {
                    "path": "LICENSE",
                    "sha": "2" * 40,
                    "html_url": "https://example.test/license",
                    "license": {"spdx_id": "Apache-2.0", "name": "Apache License 2.0"},
                },
                {"exit_code": 0},
            )
        return (
            {
                "html_url": "https://example.test/commit",
                "commit": {"verification": {"verified": False, "reason": "unsigned"}},
            },
            {"exit_code": 0},
        )

    monkeypatch.setattr("blackridge.supply_chain._json_command", json_command)
    monkeypatch.setattr("blackridge.supply_chain.DepsDevClient", _DepsDevFixture)
    monkeypatch.setattr("blackridge.supply_chain.OpenSSFScorecardClient", _ScorecardFixture)
    monkeypatch.setattr("blackridge.supply_chain._pypi_attestation_verifier", lambda: (None, None))

    def http(url: str, **_kwargs: object) -> dict[str, object]:
        if url.startswith("https://pypi.org/pypi/"):
            return {
                "status_code": 200,
                "error": None,
                "data": {
                    "urls": [
                        {
                            "filename": "fixture-1.0.whl",
                            "packagetype": "bdist_wheel",
                            "size": 123,
                            "digests": {"sha256": "3" * 64},
                            "url": "https://files.example.test/fixture.whl",
                        }
                    ]
                },
            }
        if "/integrity/" in url:
            return {"status_code": 404, "error": None, "data": {"message": "missing"}}
        name = "gpl-dependency" if "gpl-dependency" in url else "safe-dependency"
        licenses = ["GPL-3.0"] if name == "gpl-dependency" else ["MIT"]
        return {
            "status_code": 200,
            "error": None,
            "data": {"licenses": licenses, "advisoryKeys": []},
        }

    monkeypatch.setattr("blackridge.supply_chain._http_observation", http)

    probe = SupplyChainProbe().probe(
        experiment,
        work_root=work_root,
        artifact_dir=artifact_dir,
    )

    assert probe.observations["probe_completed"] is True
    assert probe.observations["source"]["observed_commit"] == experiment.commit
    assert probe.observations["dependency_licenses"]["concern_count"] == 1
    assert probe.observations["known_vulnerabilities"]["unique_primary_advisory_count"] == 1
    assert probe.observations["known_vulnerabilities"]["maximum_reported_severity"] == 8.8
    assert probe.observations["release_provenance"]["status"] == "missing"
    assert probe.observations["release_provenance"]["missing_files"] == ["fixture-1.0.whl"]
    assert probe.observations["repository_license"]["local_sha256"]
    assert any("without declared license" in warning for warning in probe.warnings)
    assert any("known vulnerabilities" in warning for warning in probe.warnings)
    assert any("pypi-attestations" in warning for warning in probe.warnings)
