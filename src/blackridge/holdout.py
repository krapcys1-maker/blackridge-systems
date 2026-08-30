"""Hash-bound verification for externally authored sealed holdout suites."""

from __future__ import annotations

import hashlib
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from blackridge.errors import BlackridgeError
from blackridge.evidence import ProbeEvidence

MANIFEST_NAME = "holdout-manifest.json"


class SealedHoldoutFile(BaseModel):
    """One immutable file named by an external holdout owner."""

    model_config = ConfigDict(extra="forbid")

    path: str
    role: Literal["definition", "evaluator", "case", "contract", "documentation"]
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=0)

    @field_validator("path")
    @classmethod
    def safe_relative_path(cls, value: str) -> str:
        if "\\" in value:
            raise ValueError("holdout paths must use POSIX separators")
        path = PurePosixPath(value)
        if (
            not value
            or value != path.as_posix()
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise ValueError("holdout paths must be normalized relative paths")
        if path.as_posix() == MANIFEST_NAME:
            raise ValueError("the manifest cannot list itself")
        return path.as_posix()


class SealedHoldoutManifest(BaseModel):
    """The complete byte inventory frozen before either experimental arm runs."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    suite_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    sealed_at: datetime
    owner: str = Field(min_length=3)
    system_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    files: list[SealedHoldoutFile] = Field(min_length=3)

    @field_validator("sealed_at")
    @classmethod
    def sealed_at_is_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("sealed_at must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def complete_unique_inventory(self) -> SealedHoldoutManifest:
        paths = [item.path for item in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("holdout file paths must be unique")
        roles = {item.role for item in self.files}
        missing_roles = {"definition", "evaluator", "case"} - roles
        if missing_roles:
            raise ValueError(
                "holdout requires definition, evaluator, and case roles; missing: "
                + ", ".join(sorted(missing_roles))
            )
        return self


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sealed_holdout(
    suite_root: Path,
    *,
    expected_manifest_sha256: str,
    expected_system_revision: str,
) -> ProbeEvidence:
    """Verify exact inventory bytes without interpreting or executing hidden cases."""

    requested_root = suite_root.absolute()
    if requested_root.is_symlink():
        raise BlackridgeError("sealed holdout root must not be a symlink")
    root = requested_root.resolve()
    if not root.is_dir():
        raise BlackridgeError("sealed holdout root must be a real directory")
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise BlackridgeError(f"sealed holdout is missing a regular {MANIFEST_NAME}")
    if len(expected_manifest_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in expected_manifest_sha256
    ):
        raise BlackridgeError("expected manifest SHA-256 must be 64 lowercase hexadecimal digits")
    actual_manifest_hash = _sha256(manifest_path)
    if actual_manifest_hash != expected_manifest_sha256:
        raise BlackridgeError("sealed holdout manifest SHA-256 does not match")
    try:
        manifest = SealedHoldoutManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
    except ValueError as exc:
        raise BlackridgeError(f"sealed holdout manifest is invalid: {exc}") from exc
    if manifest.system_revision != expected_system_revision:
        raise BlackridgeError("sealed holdout targets a different system revision")

    inventory: dict[str, Path] = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise BlackridgeError(f"sealed holdout contains a symlink: {path.relative_to(root)}")
        if path.is_file():
            inventory[path.relative_to(root).as_posix()] = path
    expected_paths = {MANIFEST_NAME, *(item.path for item in manifest.files)}
    actual_paths = set(inventory)
    missing = sorted(expected_paths - actual_paths)
    unexpected = sorted(actual_paths - expected_paths)
    if missing:
        raise BlackridgeError("sealed holdout files are missing: " + ", ".join(missing))
    if unexpected:
        raise BlackridgeError("sealed holdout contains unlisted files: " + ", ".join(unexpected))

    total_bytes = 0
    for item in manifest.files:
        path = inventory[item.path]
        size = path.stat().st_size
        if size != item.size_bytes:
            raise BlackridgeError(f"sealed holdout size does not match for {item.path}")
        if _sha256(path) != item.sha256:
            raise BlackridgeError(f"sealed holdout SHA-256 does not match for {item.path}")
        total_bytes += size
    roles = Counter(item.role for item in manifest.files)
    return ProbeEvidence(
        probe_id=uuid4().hex,
        observed_at=datetime.now(UTC),
        provider="blackridge-sealed-holdout-verifier/1",
        subject=f"{manifest.suite_id}@{manifest.version}",
        request={
            "suite_root": str(root),
            "expected_manifest_sha256": expected_manifest_sha256,
            "expected_system_revision": expected_system_revision,
        },
        observations={
            "probe_completed": True,
            "manifest_sha256": actual_manifest_hash,
            "suite_id": manifest.suite_id,
            "version": manifest.version,
            "sealed_at": manifest.sealed_at.isoformat(),
            "owner": manifest.owner,
            "system_revision": manifest.system_revision,
            "file_count": len(manifest.files),
            "case_file_count": roles["case"],
            "role_counts": dict(sorted(roles.items())),
            "total_verified_bytes": total_bytes,
            "missing_files": [],
            "unexpected_files": [],
            "symlinks": [],
        },
        sources=[str(manifest_path)],
        warnings=[
            "Byte verification does not prove evaluator independence, quality, or an L4 verdict."
        ],
    )
