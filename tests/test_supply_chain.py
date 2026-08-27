from __future__ import annotations

import subprocess

import pytest
from pydantic import ValidationError

from blackridge.errors import BlackridgeError
from blackridge.supply_chain import (
    SupplyChainExperiment,
    _inspect_checkout,
    _license_summary,
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
    subprocess.run(
        ["git", "config", "user.email", "test@example.test"], cwd=tmp_path, check=True
    )
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
