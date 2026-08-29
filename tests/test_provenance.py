from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from blackridge.errors import BlackridgeError
from blackridge.provenance import (
    ProvenanceManifest,
    _checkout_state,
    load_provenance_manifest,
    provenance_gate,
)


def complete_record(record_id: str, destination: str) -> dict[str, object]:
    return {
        "id": record_id,
        "mode": "adapted",
        "upstream_repository": "owner/project",
        "upstream_commit": "a" * 40,
        "upstream_paths": ["vendor/upstream.py"],
        "upstream_sha256": {"vendor/upstream.py": "1" * 64},
        "destination_paths": [destination],
        "destination_sha256": {destination: "2" * 64},
        "license_spdx": "MIT",
        "license_text_path": "licenses/MIT.txt",
        "license_text_sha256": "3" * 64,
        "compatibility_decision": "approved",
        "compatibility_reviewer": "security-reviewer",
        "attribution_location": "NOTICE",
        "modifications": "Renamed the public interface.",
        "manual_review_file": "reviews/copy.json",
        "manual_review_sha256": "4" * 64,
    }


def manifest_data(records: list[dict[str, object]]) -> dict[str, object]:
    return {
        "allowed_copy_licenses": ["MIT"],
        "legal_review_licenses": ["GPL-3.0-only"],
        "records": records,
    }


@pytest.mark.parametrize(
    "records",
    [
        [
            complete_record("same-id", "src/blackridge/one.py"),
            complete_record("same-id", "src/blackridge/two.py"),
        ],
        [
            complete_record("copy-one", "src/blackridge/shared.py"),
            complete_record("copy-two", "src/blackridge/shared.py"),
        ],
        [complete_record("copy-one", "src/blackridge/../blackridge/copy.py")],
    ],
)
def test_manifest_rejects_ambiguous_or_noncanonical_records(
    records: list[dict[str, object]],
) -> None:
    with pytest.raises(ValidationError):
        ProvenanceManifest.model_validate(manifest_data(records))


def test_manifest_rejects_overlapping_license_policies() -> None:
    data = manifest_data([])
    data["legal_review_licenses"] = ["MIT"]

    with pytest.raises(ValidationError):
        ProvenanceManifest.model_validate(data)


def test_empty_reviewed_registry_allows_no_copy(tmp_path: Path) -> None:
    source = tmp_path / "src" / "blackridge"
    source.mkdir(parents=True)
    (source / "original.py").write_text("VALUE = 1\n", encoding="utf-8")
    manifest = load_provenance_manifest(Path("provenance/derived-code.yaml"))

    probe = provenance_gate(manifest, repo_root=tmp_path)

    assert probe.observations["gate_allows_copy"] is True
    assert probe.observations["issue_count"] == 0


def test_unregistered_derived_marker_is_blocked(tmp_path: Path) -> None:
    source = tmp_path / "src" / "blackridge"
    source.mkdir(parents=True)
    (source / "copy.py").write_text("# Derived from: upstream/file.py\n", encoding="utf-8")
    manifest = load_provenance_manifest(Path("provenance/derived-code.yaml"))

    probe = provenance_gate(manifest, repo_root=tmp_path)

    assert probe.observations["gate_allows_copy"] is False
    assert "unregistered derived-code marker" in probe.observations["issues"][0]["issue"]


def test_incomplete_copy_record_retains_each_missing_control(tmp_path: Path) -> None:
    manifest = load_provenance_manifest(Path("provenance/derived-code-invalid-example.yaml"))

    probe = provenance_gate(manifest, repo_root=tmp_path)
    issues = [item["issue"] for item in probe.observations["issues"]]

    assert probe.observations["gate_allows_copy"] is False
    assert "upstream_commit must be an immutable 40-character commit" in issues
    assert "missing license_spdx" in issues
    assert "missing manual_review_file" in issues
    assert "license compatibility is not approved" in issues


def test_upstream_checkout_state_rejects_ignored_residue(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (tmp_path / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "fixture"], cwd=tmp_path, check=True)

    assert _checkout_state(tmp_path)["pristine"] is True
    (tmp_path / "ignored.txt").write_text("stale\n", encoding="utf-8")

    with pytest.raises(BlackridgeError, match="not pristine"):
        _checkout_state(tmp_path)
