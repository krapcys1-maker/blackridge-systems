"""Source-origin audit and a fail-closed gate for copied or adapted code."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from blackridge.errors import BlackridgeError
from blackridge.evidence import ProbeEvidence
from blackridge.formats import load_yaml
from blackridge.git_integrity import inspect_pristine_checkout
from blackridge.process_boundary import run_bounded

PROVENANCE_SOURCE = (
    "https://github.com/krapcys1-maker/blackridge-systems/blob/main/src/blackridge/provenance.py"
)
_COMMIT = re.compile(r"^[a-f0-9]{40}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_DERIVED_MARKER = re.compile(r"(?i)(derived|adapted|copied)\s+from\s*:")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(argv: list[str], *, cwd: Path | None = None) -> str:
    completed = run_bounded(argv, cwd=cwd)
    if completed.timed_out:
        raise BlackridgeError(f"command timed out: {argv[0]}")
    if completed.output_limit_exceeded:
        raise BlackridgeError(f"command exceeded the output limit: {argv[0]}")
    if completed.returncode != 0:
        raise BlackridgeError(
            f"command failed with exit {completed.returncode}: {argv[0]} {completed.stderr.strip()}"
        )
    return completed.stdout


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UpstreamReference(StrictModel):
    """One immutable upstream tree used as a similarity reference."""

    id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    version: str = Field(min_length=1)
    license_spdx: str = Field(min_length=2)


class SourceAuditDefinition(StrictModel):
    """Frozen scope for a source-history and exact-fragment audit."""

    schema_version: Literal["1"] = "1"
    source_glob: str = "src/blackridge/*.py"
    exact_window_lines: int = Field(default=6, ge=4, le=20)
    upstreams: list[UpstreamReference] = Field(min_length=1)


class DerivedCodeRecord(StrictModel):
    """Evidence required before upstream source may enter this repository."""

    id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    mode: Literal["copied", "adapted"]
    upstream_repository: str | None = None
    upstream_commit: str | None = None
    upstream_paths: list[str] = Field(default_factory=list)
    upstream_sha256: dict[str, str] = Field(default_factory=dict)
    destination_paths: list[str] = Field(default_factory=list)
    destination_sha256: dict[str, str] = Field(default_factory=dict)
    license_spdx: str | None = None
    license_text_path: str | None = None
    license_text_sha256: str | None = None
    compatibility_decision: Literal["approved", "rejected", "unreviewed"] = "unreviewed"
    compatibility_reviewer: str | None = None
    attribution_location: str | None = None
    modifications: str | None = None
    manual_review_file: str | None = None
    manual_review_sha256: str | None = None


class ProvenanceManifest(StrictModel):
    """Copy policy and the complete registry of derived Blackridge files."""

    schema_version: Literal["1"] = "1"
    allowed_copy_licenses: list[str]
    legal_review_licenses: list[str]
    records: list[DerivedCodeRecord] = Field(default_factory=list)


def load_source_audit_definition(path: Path) -> SourceAuditDefinition:
    return SourceAuditDefinition.model_validate(load_yaml(path))


def load_provenance_manifest(path: Path) -> ProvenanceManifest:
    return ProvenanceManifest.model_validate(load_yaml(path))


def _checkout_state(
    destination: Path,
    *,
    expected_commit: str | None = None,
    expected_tree: str | None = None,
) -> dict[str, object]:
    _, state = inspect_pristine_checkout(
        destination,
        expected_commit=expected_commit,
        expected_tree=expected_tree,
        context=f"upstream checkout {destination}",
    )
    return state


def _exact_checkout(reference: UpstreamReference, root: Path) -> tuple[Path, dict[str, object]]:
    destination = root / reference.id
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not (destination / ".git").is_dir() and any(destination.iterdir()):
        raise BlackridgeError(f"non-empty audit directory is not Git: {destination}")
    if not (destination / ".git").is_dir():
        _run(["git", "init", str(destination)])
        _run(
            [
                "git",
                "-C",
                str(destination),
                "remote",
                "add",
                "origin",
                f"https://github.com/{reference.repository}.git",
            ]
        )
    else:
        _checkout_state(destination)
    remote = _run(["git", "-C", str(destination), "remote", "get-url", "origin"]).strip()
    expected = f"https://github.com/{reference.repository}.git"
    if remote.removesuffix(".git") != expected.removesuffix(".git"):
        raise BlackridgeError(f"wrong origin in existing audit directory: {destination}")
    _run(["git", "-C", str(destination), "fetch", "--depth", "1", "origin", reference.commit])
    _run(["git", "-C", str(destination), "checkout", "--detach", "FETCH_HEAD"])
    state = _checkout_state(destination, expected_commit=reference.commit)
    return destination, state


def _normalized_lines(path: Path) -> list[tuple[int, str]]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    normalized: list[tuple[int, str]] = []
    for number, line in enumerate(text.splitlines(), 1):
        value = " ".join(line.strip().split())
        if value and not value.startswith("#"):
            normalized.append((number, value))
    return normalized


def _windows(path: Path, width: int) -> list[tuple[tuple[str, ...], int, int]]:
    lines = _normalized_lines(path)
    return [
        (
            tuple(value for _, value in lines[index : index + width]),
            lines[index][0],
            lines[index + width - 1][0],
        )
        for index in range(max(0, len(lines) - width + 1))
    ]


def _first_add(repo_root: Path, relative: str) -> dict[str, str | None]:
    output = _run(
        [
            "git",
            "log",
            "--diff-filter=A",
            "--follow",
            "--format=%H|%aI|%an|%s",
            "--",
            relative,
        ],
        cwd=repo_root,
    )
    rows = [line for line in output.splitlines() if line]
    if not rows:
        return {"commit": None, "authored_at": None, "author": None, "subject": None}
    commit, authored_at, author, subject = rows[-1].split("|", 3)
    return {
        "commit": commit,
        "authored_at": authored_at,
        "author": author,
        "subject": subject,
    }


def audit_source_provenance(
    definition: SourceAuditDefinition,
    *,
    repo_root: Path,
    work_root: Path,
) -> ProbeEvidence:
    """Inspect history, attribution markers, and exact multi-line upstream matches."""

    repo_root = repo_root.resolve()
    work_root = work_root.resolve()
    if not (repo_root / ".git").is_dir():
        raise BlackridgeError(f"not a Git repository: {repo_root}")
    matched_paths = [path.resolve() for path in repo_root.glob(definition.source_glob)]
    if any(not path.is_relative_to(repo_root) for path in matched_paths):
        raise BlackridgeError("source provenance glob resolves outside the repository root")
    relative_files = sorted(path.relative_to(repo_root).as_posix() for path in matched_paths)
    tracked = set(_run(["git", "ls-files"], cwd=repo_root).splitlines())
    untracked = [path for path in relative_files if path not in tracked]
    file_rows: list[dict[str, object]] = []
    local_windows: dict[tuple[str, ...], list[tuple[str, int, int]]] = defaultdict(list)
    marker_findings: list[dict[str, object]] = []
    for relative in relative_files:
        path = repo_root / relative
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), 1):
            if _DERIVED_MARKER.search(line):
                marker_findings.append(
                    {"file": relative, "line": line_number, "text": line.strip()}
                )
        for window, start, end in _windows(path, definition.exact_window_lines):
            local_windows[window].append((relative, start, end))
        file_rows.append(
            {
                "path": relative,
                "sha256": _sha256(path),
                "line_count": len(text.splitlines()),
                "first_add": _first_add(repo_root, relative),
            }
        )

    matches: list[dict[str, object]] = []
    upstream_rows: list[dict[str, object]] = []
    extensions = {".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}
    for reference in definition.upstreams:
        checkout, checkout_before = _exact_checkout(reference, work_root)
        scanned_files = 0
        skipped_symlinks: list[str] = []
        for upstream_file in checkout.rglob("*"):
            if upstream_file.is_symlink():
                skipped_symlinks.append(upstream_file.relative_to(checkout).as_posix())
                continue
            if not upstream_file.is_file() or upstream_file.suffix.lower() not in extensions:
                continue
            if ".git" in upstream_file.parts:
                continue
            scanned_files += 1
            for window, start, end in _windows(upstream_file, definition.exact_window_lines):
                local_hits = local_windows.get(window)
                if not local_hits:
                    continue
                for local_file, local_start, local_end in local_hits:
                    matches.append(
                        {
                            "local_file": local_file,
                            "local_start": local_start,
                            "local_end": local_end,
                            "upstream_id": reference.id,
                            "upstream_file": upstream_file.relative_to(checkout).as_posix(),
                            "upstream_start": start,
                            "upstream_end": end,
                            "window_sha256": hashlib.sha256("\n".join(window).encode()).hexdigest(),
                        }
                    )
        checkout_after = _checkout_state(
            checkout,
            expected_commit=reference.commit,
            expected_tree=str(checkout_before["tree"]),
        )
        upstream_rows.append(
            {
                **reference.model_dump(),
                "checkout": str(checkout),
                "observed_commit": checkout_before["commit"],
                "tree": checkout_before["tree"],
                "checkout_before_scan": checkout_before,
                "checkout_after_scan": checkout_after,
                "scanned_files": scanned_files,
                "skipped_symlinks": skipped_symlinks,
            }
        )

    return ProbeEvidence(
        probe_id=uuid4().hex,
        observed_at=datetime.now(UTC),
        provider="blackridge-source-provenance-audit/1",
        subject=f"{repo_root}@{_run(['git', 'rev-parse', 'HEAD'], cwd=repo_root).strip()}",
        request=definition.model_dump(),
        observations={
            "probe_completed": True,
            "tracked_source_file_count": len(relative_files) - len(untracked),
            "untracked_source_files": untracked,
            "files": file_rows,
            "derived_attribution_markers": marker_findings,
            "exact_window_lines": definition.exact_window_lines,
            "upstreams": upstream_rows,
            "exact_fragment_match_count": len(matches),
            "exact_fragment_matches": matches,
            "limitations": [
                (
                    "An exact normalized-line scan does not detect renamed, reordered, "
                    "translated, or heavily edited copies."
                ),
                (
                    "Git history proves when content entered this repository, not who "
                    "originally authored similar ideas."
                ),
                (
                    "A zero-match result is evidence within this frozen scope, not a legal "
                    "originality guarantee."
                ),
            ],
        },
        sources=[
            f"https://github.com/{item.repository}/commit/{item.commit}"
            for item in definition.upstreams
        ]
        + [PROVENANCE_SOURCE],
        warnings=[
            "This probe assigns no legal or release verdict; inspect matches and "
            "limitations manually."
        ],
    )


def _path_issue(repo_root: Path, relative: str, expected: str | None, label: str) -> list[str]:
    path = (repo_root / relative).resolve()
    if not path.is_relative_to(repo_root):
        return [f"{label} escapes the repository: {relative}"]
    if not path.is_file():
        return [f"{label} does not exist: {relative}"]
    if expected is None or not _SHA256.fullmatch(expected):
        return [f"{label} has no valid recorded SHA-256: {relative}"]
    observed = _sha256(path)
    return [] if observed == expected else [f"{label} SHA-256 mismatch: {relative}"]


def provenance_gate(manifest: ProvenanceManifest, *, repo_root: Path) -> ProbeEvidence:
    """Validate every registered copy and flag unregistered derived-code markers."""

    repo_root = repo_root.resolve()
    issues: list[dict[str, object]] = []
    registered_destinations: set[str] = set()
    record_rows: list[dict[str, object]] = []
    for record in manifest.records:
        record_issues: list[str] = []
        required_text = {
            "upstream_repository": record.upstream_repository,
            "license_spdx": record.license_spdx,
            "license_text_path": record.license_text_path,
            "compatibility_reviewer": record.compatibility_reviewer,
            "attribution_location": record.attribution_location,
            "modifications": record.modifications,
            "manual_review_file": record.manual_review_file,
        }
        record_issues.extend(
            f"missing {name}" for name, value in required_text.items() if not value
        )
        if not record.upstream_commit or not _COMMIT.fullmatch(record.upstream_commit):
            record_issues.append("upstream_commit must be an immutable 40-character commit")
        if not record.upstream_paths:
            record_issues.append("missing upstream_paths")
        if not record.destination_paths:
            record_issues.append("missing destination_paths")
        if record.compatibility_decision != "approved":
            record_issues.append("license compatibility is not approved")
        if record.license_spdx in manifest.legal_review_licenses:
            record_issues.append(
                f"license requires a separate legal decision: {record.license_spdx}"
            )
        elif record.license_spdx not in manifest.allowed_copy_licenses:
            record_issues.append(
                f"license is not allowed for copying: {record.license_spdx or 'unknown'}"
            )
        for path in record.upstream_paths:
            record_issues.extend(
                _path_issue(repo_root, path, record.upstream_sha256.get(path), "upstream snapshot")
            )
        for path in record.destination_paths:
            registered_destinations.add(path)
            record_issues.extend(
                _path_issue(repo_root, path, record.destination_sha256.get(path), "destination")
            )
        if record.license_text_path:
            record_issues.extend(
                _path_issue(
                    repo_root,
                    record.license_text_path,
                    record.license_text_sha256,
                    "license text",
                )
            )
        if record.manual_review_file:
            record_issues.extend(
                _path_issue(
                    repo_root,
                    record.manual_review_file,
                    record.manual_review_sha256,
                    "manual review",
                )
            )
            review_path = (repo_root / record.manual_review_file).resolve()
            if review_path.is_file():
                try:
                    review = json.loads(review_path.read_text(encoding="utf-8"))
                    if review.get("verdict") != "pass":
                        record_issues.append("manual review verdict is not pass")
                except (OSError, json.JSONDecodeError):
                    record_issues.append("manual review is not valid JSON")
        record_rows.append({"id": record.id, "mode": record.mode, "issues": record_issues})
        issues.extend({"record": record.id, "issue": value} for value in record_issues)

    marker_rows: list[dict[str, object]] = []
    for marker_path in sorted((repo_root / "src" / "blackridge").glob("*.py")):
        relative = marker_path.relative_to(repo_root).as_posix()
        for line_number, line in enumerate(marker_path.read_text(encoding="utf-8").splitlines(), 1):
            if _DERIVED_MARKER.search(line):
                marker = {
                    "file": relative,
                    "line": line_number,
                    "registered": relative in registered_destinations,
                }
                marker_rows.append(marker)
                if not marker["registered"]:
                    issues.append(
                        {
                            "record": None,
                            "issue": f"unregistered derived-code marker: {relative}:{line_number}",
                        }
                    )

    return ProbeEvidence(
        probe_id=uuid4().hex,
        observed_at=datetime.now(UTC),
        provider="blackridge-copy-provenance-gate/1",
        subject=str(repo_root),
        request={"manifest": manifest.model_dump()},
        observations={
            "probe_completed": True,
            "record_count": len(manifest.records),
            "records": record_rows,
            "derived_markers": marker_rows,
            "issue_count": len(issues),
            "issues": issues,
            "gate_allows_copy": len(issues) == 0,
            "scope_warning": (
                "The registry gate validates declared copies. Pair it with the source "
                "similarity audit because an undeclared copy can omit its attribution marker."
            ),
        },
        sources=[PROVENANCE_SOURCE, "https://www.apache.org/licenses/LICENSE-2.0"],
        warnings=[] if not issues else ["Copy/adaptation provenance gate has unresolved issues."],
    )
