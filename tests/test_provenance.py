from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from blackridge.errors import BlackridgeError
from blackridge.provenance import _checkout_state, load_provenance_manifest, provenance_gate


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
    subprocess.run(
        ["git", "config", "user.email", "test@example.test"], cwd=tmp_path, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (tmp_path / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "fixture"], cwd=tmp_path, check=True)

    assert _checkout_state(tmp_path)["pristine"] is True
    (tmp_path / "ignored.txt").write_text("stale\n", encoding="utf-8")

    with pytest.raises(BlackridgeError, match="not pristine"):
        _checkout_state(tmp_path)
