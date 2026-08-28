from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from blackridge.errors import BlackridgeError
from blackridge.holdout import MANIFEST_NAME, verify_sealed_holdout

REVISION = "a" * 40


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _suite(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "sealed-suite"
    files = {
        "definition.json": ("definition", b'{"suite":"fixture"}\n'),
        "evaluator.py": ("evaluator", b"raise SystemExit('external evaluator fixture')\n"),
        "cases/hidden.bin": ("case", b"opaque-case-bytes\x00\xff"),
        "contracts/output.json": ("contract", b'{"type":"object"}\n'),
    }
    entries = []
    for relative, (role, content) in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        entries.append(
            {
                "path": relative,
                "role": role,
                "sha256": _sha256(path),
                "size_bytes": len(content),
            }
        )
    manifest = {
        "schema_version": "1",
        "suite_id": "external-fixture-holdout",
        "version": "2026.08.27",
        "sealed_at": datetime(2026, 8, 27, tzinfo=UTC).isoformat(),
        "owner": "Independent fixture evaluator",
        "system_revision": REVISION,
        "files": entries,
    }
    manifest_path = root / MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return root, _sha256(manifest_path)


def test_sealed_holdout_verifies_exact_bytes_without_interpreting_cases(tmp_path: Path) -> None:
    root, manifest_hash = _suite(tmp_path)

    probe = verify_sealed_holdout(
        root,
        expected_manifest_sha256=manifest_hash,
        expected_system_revision=REVISION,
    )

    assert probe.observations["probe_completed"] is True
    assert probe.observations["file_count"] == 4
    assert probe.observations["case_file_count"] == 1
    assert probe.observations["role_counts"] == {
        "case": 1,
        "contract": 1,
        "definition": 1,
        "evaluator": 1,
    }
    assert probe.observations["missing_files"] == []
    assert probe.observations["unexpected_files"] == []
    assert "does not prove evaluator independence" in probe.warnings[0]


def test_sealed_holdout_rejects_manifest_and_revision_mismatch(tmp_path: Path) -> None:
    root, manifest_hash = _suite(tmp_path)

    with pytest.raises(BlackridgeError, match="manifest SHA-256 does not match"):
        verify_sealed_holdout(
            root,
            expected_manifest_sha256="b" * 64,
            expected_system_revision=REVISION,
        )
    with pytest.raises(BlackridgeError, match="different system revision"):
        verify_sealed_holdout(
            root,
            expected_manifest_sha256=manifest_hash,
            expected_system_revision="c" * 40,
        )


@pytest.mark.parametrize("mutation", ["bytes", "extra", "missing"])
def test_sealed_holdout_rejects_inventory_mutation(tmp_path: Path, mutation: str) -> None:
    root, manifest_hash = _suite(tmp_path)
    if mutation == "bytes":
        (root / "cases" / "hidden.bin").write_bytes(b"changed but same size!!")
        expected = "size does not match|SHA-256 does not match"
    elif mutation == "extra":
        (root / "unlisted.txt").write_text("not sealed", encoding="utf-8")
        expected = "contains unlisted files"
    else:
        (root / "evaluator.py").unlink()
        expected = "files are missing"

    with pytest.raises(BlackridgeError, match=expected):
        verify_sealed_holdout(
            root,
            expected_manifest_sha256=manifest_hash,
            expected_system_revision=REVISION,
        )


def test_sealed_holdout_rejects_symlinks_when_supported(tmp_path: Path) -> None:
    root, manifest_hash = _suite(tmp_path)
    target = root / "definition.json"
    link = root / "unlisted-link"
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")

    with pytest.raises(BlackridgeError, match="contains a symlink"):
        verify_sealed_holdout(
            root,
            expected_manifest_sha256=manifest_hash,
            expected_system_revision=REVISION,
        )


def test_sealed_holdout_rejects_a_root_symlink_when_supported(tmp_path: Path) -> None:
    root, manifest_hash = _suite(tmp_path)
    link = tmp_path / "suite-link"
    try:
        link.symlink_to(root, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks are unavailable: {exc}")

    with pytest.raises(BlackridgeError, match="root must not be a symlink"):
        verify_sealed_holdout(
            link,
            expected_manifest_sha256=manifest_hash,
            expected_system_revision=REVISION,
        )


def test_sealed_holdout_rejects_non_normalized_manifest_paths(tmp_path: Path) -> None:
    root, _ = _suite(tmp_path)
    manifest_path = root / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["path"] = "nested//definition.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(BlackridgeError, match="manifest is invalid"):
        verify_sealed_holdout(
            root,
            expected_manifest_sha256=_sha256(manifest_path),
            expected_system_revision=REVISION,
        )
