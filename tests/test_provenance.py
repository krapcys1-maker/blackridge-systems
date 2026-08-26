from __future__ import annotations

from pathlib import Path

from blackridge.provenance import load_provenance_manifest, provenance_gate


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
