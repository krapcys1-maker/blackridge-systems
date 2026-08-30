"""Freeze the supply-chain auditor system definition against the current component bytes.

The definition locks each component's SHA-256. Any edit to a component — including a
formatter pass — invalidates the lock and the solver refuses to select it, which is the
intended behaviour. Re-running this tool is the deliberate act of re-freezing after a
reviewed change.

`--check` reports drift without writing, which is the form suitable for CI.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from typing import Any

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SYSTEM_DIRECTORY = REPOSITORY_ROOT / "systems" / "supply-chain-auditor-v1"
SCHEMA = "https://json-schema.org/draft/2020-12/schema"

COMPONENTS: dict[str, tuple[str, str, list[str], str]] = {
    "osv-scanner": (
        "vulnerability-scan",
        "components/osv_scanner_v1/osv_scanner.py",
        ["audit-request/v1"],
        "vulnerability-report/v1",
    ),
    "scorecard-posture": (
        "security-posture",
        "components/scorecard_posture_v1/scorecard_posture.py",
        ["audit-request/v1"],
        "security-posture/v1",
    ),
    "audit-merger": (
        "audit-merge",
        "components/audit_merger_v1/audit_merger.py",
        ["vulnerability-report/v1", "security-posture/v1"],
        "audit-report/v1",
    ),
}


def _sha256(relative: str) -> str:
    return hashlib.sha256((REPOSITORY_ROOT / relative).read_bytes()).hexdigest()


def _component(component_id: str) -> dict[str, Any]:
    capability_id, relative, accepts, produces = COMPONENTS[component_id]
    path = "{definition_dir}/../../" + relative
    digest = _sha256(relative)
    return {
        "component_id": component_id,
        "capability_id": capability_id,
        "source_uri": f"blackridge://components/{component_id}",
        "revision": f"sha256:{digest}",
        "license_spdx": "Apache-2.0",
        "integration": "command-json",
        "accepts": accepts,
        "produces": [produces],
        "launch": {
            "argv": ["{python}", path],
            "artifact_file": path,
            "artifact_sha256": digest,
            "working_directory": "{definition_dir}",
            "timeout_seconds": 60,
            "environment_allowlist": [],
        },
        "evidence": {"level": 0},
        "selection_priority": 10,
    }


def _contracts() -> list[dict[str, Any]]:
    return [
        {
            "contract_id": "audit-request/v1",
            "schema": {
                "$schema": SCHEMA,
                "type": "object",
                "additionalProperties": True,
                "required": ["schema_version", "request_id", "repository", "packages"],
                "properties": {
                    "schema_version": {"const": "1"},
                    "request_id": {"type": "string", "minLength": 1},
                    "repository": {
                        "type": "string",
                        "pattern": "^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$",
                    },
                    "packages": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "required": ["ecosystem", "name", "version"],
                            "properties": {
                                "ecosystem": {"type": "string"},
                                "name": {"type": "string"},
                                "version": {"type": "string"},
                            },
                        },
                    },
                },
            },
        },
        {
            "contract_id": "vulnerability-report/v1",
            "schema": {
                "$schema": SCHEMA,
                "type": "object",
                "additionalProperties": True,
                "required": [
                    "schema_version",
                    "request_id",
                    "status",
                    "packages",
                    "total_vulnerabilities",
                    "unknown",
                ],
                "properties": {
                    "schema_version": {"const": "1"},
                    "request_id": {"type": "string"},
                    "status": {"enum": ["ok", "partial"]},
                    "packages": {"type": "array"},
                    "total_vulnerabilities": {"type": "integer", "minimum": 0},
                    "unknown": {"type": "array"},
                },
            },
        },
        {
            "contract_id": "security-posture/v1",
            "schema": {
                "$schema": SCHEMA,
                "type": "object",
                "additionalProperties": True,
                "required": [
                    "schema_version",
                    "request_id",
                    "repository",
                    "status",
                    "score",
                    "checks",
                ],
                "properties": {
                    "schema_version": {"const": "1"},
                    "request_id": {"type": "string"},
                    "repository": {"type": "string"},
                    "status": {"enum": ["ok", "unavailable"]},
                    "score": {"type": ["number", "null"]},
                    "checks": {"type": "array"},
                },
            },
        },
        {
            "contract_id": "audit-report/v1",
            "schema": {
                "$schema": SCHEMA,
                "type": "object",
                "additionalProperties": True,
                "required": [
                    "schema_version",
                    "request_id",
                    "verdict",
                    "vulnerability_count",
                    "unknown",
                    "limitations",
                ],
                "properties": {
                    "schema_version": {"const": "1"},
                    "request_id": {"type": "string"},
                    "verdict": {"enum": ["clean", "findings", "unknown"]},
                    "vulnerability_count": {"type": "integer", "minimum": 0},
                    "unknown": {"type": "array"},
                    "limitations": {"type": "array", "minItems": 1},
                },
            },
        },
    ]


def build_definition() -> dict[str, Any]:
    return {
        "schema_version": "1",
        "name": "supply-chain-auditor",
        "goal": (
            "Audit one repository's declared dependency versions for published vulnerabilities "
            "and report its independent security posture in a single deterministic verdict."
        ),
        "mode": "calibration",
        "external_input": "audit-request/v1",
        "required_output": "audit-report/v1",
        "required_capabilities": [entry[0] for entry in COMPONENTS.values()],
        "allowed_licenses": ["Apache-2.0"],
        "allowed_integrations": ["command-json"],
        "minimum_evidence_level": 0,
        "contracts": _contracts(),
        "components": [_component(component_id) for component_id in COMPONENTS],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail instead of writing on drift.")
    args = parser.parse_args()

    SYSTEM_DIRECTORY.mkdir(parents=True, exist_ok=True)
    path = SYSTEM_DIRECTORY / "definition.yaml"
    rendered = yaml.safe_dump(build_definition(), sort_keys=False, width=100).encode("utf-8")
    previous = path.read_bytes() if path.is_file() else None

    if previous == rendered:
        print("definition is current")
        return 0
    if args.check:
        print("definition would change; component bytes drifted from the frozen locks")
        return 1
    path.write_bytes(rendered)
    print(f"definition re-frozen: {path.relative_to(REPOSITORY_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
