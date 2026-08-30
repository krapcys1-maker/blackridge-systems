#!/usr/bin/env python3
"""Merge independent audit observations into one deterministic verdict.

This is the glue capability. No published component implements it, because the contracts it
joins are specific to this system — discovery correctly finds nothing for it, and it is
written rather than found.

Reads the composition fan-in envelope `{"inputs": {contract_id: artifact}}` on stdin and
writes one `audit-report/v1` object on stdout.
"""

from __future__ import annotations

import json
import sys
from typing import Any

VULNERABILITY_CONTRACT = "vulnerability-report/v1"
POSTURE_CONTRACT = "security-posture/v1"


class ContractError(ValueError):
    """The caller supplied an envelope outside the frozen component contract."""


def _input(envelope: dict[str, Any], contract_id: str) -> dict[str, Any]:
    inputs = envelope.get("inputs")
    if not isinstance(inputs, dict):
        raise ContractError("envelope must carry an 'inputs' object")
    value = inputs.get(contract_id)
    if not isinstance(value, dict):
        raise ContractError(f"missing required input {contract_id}")
    return value


def merge(envelope: dict[str, Any]) -> dict[str, Any]:
    """Combine the vulnerability and posture observations without inventing certainty."""

    vulnerabilities = _input(envelope, VULNERABILITY_CONTRACT)
    posture = _input(envelope, POSTURE_CONTRACT)

    request_id = vulnerabilities.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        raise ContractError("vulnerability report has no request_id")
    if posture.get("request_id") != request_id:
        raise ContractError("posture and vulnerability reports describe different requests")

    unknown: list[str] = []
    for item in vulnerabilities.get("unknown", []) or []:
        if isinstance(item, str):
            unknown.append(f"vulnerability: {item}")
    if posture.get("status") != "ok":
        unknown.append(f"posture: {posture.get('error') or 'unavailable'}")

    packages = vulnerabilities.get("packages")
    package_list = packages if isinstance(packages, list) else []
    affected = sorted(
        f"{item.get('ecosystem')}/{item.get('name')}@{item.get('version')}"
        for item in package_list
        if isinstance(item, dict) and item.get("vulnerabilities")
    )
    total = vulnerabilities.get("total_vulnerabilities")
    vulnerability_count = total if isinstance(total, int) else 0

    # An incomplete audit is never "clean". Absence of evidence is reported as unknown.
    if unknown:
        verdict = "findings" if vulnerability_count else "unknown"
    else:
        verdict = "findings" if vulnerability_count else "clean"

    return {
        "schema_version": "1",
        "request_id": request_id,
        "repository": posture.get("repository"),
        "verdict": verdict,
        "vulnerability_count": vulnerability_count,
        "affected_packages": affected,
        "packages_audited": len(package_list),
        "posture_score": posture.get("score"),
        "posture_status": posture.get("status"),
        "unknown": sorted(unknown),
        "sources": {
            "vulnerabilities": vulnerabilities.get("source"),
            "posture": posture.get("source"),
        },
        "limitations": [
            "A technical dependency verdict is not legal approval.",
            "Only declared package versions supplied in the request were audited.",
            "An absent published scorecard is reported as unknown, not as a passing posture.",
        ],
    }


def main() -> int:
    try:
        envelope = json.load(sys.stdin)
        if not isinstance(envelope, dict):
            raise ContractError("input must be one JSON object")
        report = merge(envelope)
    except ContractError as exc:
        print(json.dumps({"schema_version": "1", "verdict": "error", "error": str(exc)}))
        return 1
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(
            json.dumps({"schema_version": "1", "verdict": "error", "error": f"invalid JSON: {exc}"})
        )
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
