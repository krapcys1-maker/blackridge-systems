#!/usr/bin/env python3
"""Evaluate a wheel inventory against an explicit request policy."""

from __future__ import annotations

import json
import re
import sys
from typing import Any


def _normalized_distribution(requirement: str) -> str:
    match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", requirement.strip())
    if match is None:
        raise ValueError(f"cannot parse Requires-Dist entry: {requirement!r}")
    return re.sub(r"[-_.]+", "-", match.group(1)).lower()


def _check(check_id: str, expected: object, observed: object, passed: bool) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "passed": passed,
        "expected": expected,
        "observed": observed,
    }


def evaluate(payload: dict[str, Any]) -> dict[str, Any]:
    inputs = payload["inputs"]
    request = inputs["wheel-audit-request/v1"]
    inventory = inputs["wheel-inventory/v1"]
    if request["request_id"] != inventory["request_id"]:
        raise ValueError("request identity does not match wheel inventory identity")

    metadata = inventory["metadata"]
    archive = inventory["archive"]
    artifact = inventory["artifact"]
    license_kinds = sorted({item["kind"] for item in inventory["license_files"]})
    dependencies = sorted(
        {_normalized_distribution(item) for item in metadata["requires_dist"]}
    )
    forbidden = sorted(
        re.sub(r"[-_.]+", "-", item).lower()
        for item in request["forbidden_dependency_names"]
    )
    forbidden_present = sorted(set(dependencies) & set(forbidden))
    checks = [
        _check(
            "artifact-sha256",
            request["expected_wheel_sha256"],
            artifact["sha256"],
            artifact["sha256"] == request["expected_wheel_sha256"],
        ),
        _check(
            "project-name",
            request["expected_project_name"],
            metadata["name"],
            metadata["name"].lower() == request["expected_project_name"].lower(),
        ),
        _check(
            "project-version",
            request["expected_version"],
            metadata["version"],
            metadata["version"] == request["expected_version"],
        ),
        _check(
            "project-license",
            sorted(request["allowed_project_licenses"]),
            metadata["license"],
            metadata["license"] in request["allowed_project_licenses"],
        ),
        _check(
            "required-license-files",
            sorted(request["required_license_kinds"]),
            license_kinds,
            set(request["required_license_kinds"]).issubset(license_kinds),
        ),
        _check("archive-path-safety", [], archive["unsafe_paths"], not archive["unsafe_paths"]),
        _check(
            "archive-name-uniqueness",
            [],
            archive["duplicate_names"],
            not archive["duplicate_names"],
        ),
        _check("record-integrity", [], archive["record_errors"], archive["record_valid"]),
        _check("forbidden-dependencies", [], forbidden_present, not forbidden_present),
    ]
    failed = [item["check_id"] for item in checks if not item["passed"]]
    status = "policy-passed" if not failed else "policy-failed"
    return {
        "schema_version": "1",
        "request_id": request["request_id"],
        "status": status,
        "summary": (
            "The inspected wheel satisfies every declared technical policy check."
            if not failed
            else "The inspected wheel failed declared checks: " + ", ".join(failed) + "."
        ),
        "artifact": artifact,
        "checks": checks,
        "dependency_names": dependencies,
        "license_files": inventory["license_files"],
        "release_ready": False,
        "release_blockers": [
            "A technical wheel policy pass is not legal approval or a vulnerability verdict.",
            "The runtime image and declared dependencies require separate distribution evidence.",
        ],
    }


def main() -> None:
    payload = json.load(sys.stdin)
    print(json.dumps(evaluate(payload), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
