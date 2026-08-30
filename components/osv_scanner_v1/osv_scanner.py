#!/usr/bin/env python3
"""Query OSV for known vulnerabilities affecting exact package versions.

Reads one `audit-request/v1` object on stdin and writes one `vulnerability-report/v1`
object on stdout. This is the JSON-in/JSON-out boundary over the public OSV database that
`google/osv-scanner` also consumes.

A `replay` block in the request supplies recorded OSV responses instead of calling the
network, so the component runs unchanged in a networkless sandbox.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from typing import Any

OSV_QUERY_URL = "https://api.osv.dev/v1/query"
MAX_PACKAGES = 500
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
TIMEOUT_SECONDS = 20


class ContractError(ValueError):
    """The caller supplied a request outside the frozen component contract."""


def _text(value: object, name: str, maximum: int = 256) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{name} must be a non-empty string")
    if len(value) > maximum:
        raise ContractError(f"{name} exceeds {maximum} characters")
    return value


def _packages(request: dict[str, Any]) -> list[dict[str, str]]:
    raw = request.get("packages")
    if not isinstance(raw, list) or not raw:
        raise ContractError("packages must be a non-empty array")
    if len(raw) > MAX_PACKAGES:
        raise ContractError(f"packages exceeds {MAX_PACKAGES} entries")
    packages = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ContractError(f"packages[{index}] must be an object")
        packages.append(
            {
                "ecosystem": _text(item.get("ecosystem"), f"packages[{index}].ecosystem", 64),
                "name": _text(item.get("name"), f"packages[{index}].name", 214),
                "version": _text(item.get("version"), f"packages[{index}].version", 128),
            }
        )
    return packages


def _severity(entry: dict[str, Any]) -> str | None:
    """Prefer an explicit database severity, then any CVSS vector, else unknown."""

    database = entry.get("database_specific")
    if isinstance(database, dict):
        value = database.get("severity")
        if isinstance(value, str) and value:
            return value
    severities = entry.get("severity")
    if isinstance(severities, list):
        for item in severities:
            if isinstance(item, dict) and isinstance(item.get("score"), str):
                return item["score"]
    return None


def _normalize(vulns: object) -> list[dict[str, Any]]:
    if not isinstance(vulns, list):
        return []
    normalized = []
    for entry in vulns:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            continue
        aliases = entry.get("aliases")
        normalized.append(
            {
                "id": entry["id"],
                "summary": entry.get("summary") if isinstance(entry.get("summary"), str) else None,
                "severity": _severity(entry),
                "aliases": sorted(a for a in aliases if isinstance(a, str))
                if isinstance(aliases, list)
                else [],
            }
        )
    # Deterministic order regardless of upstream response ordering.
    return sorted(normalized, key=lambda item: item["id"])


def _query_osv(package: dict[str, str]) -> tuple[list[dict[str, Any]], str | None]:
    body = json.dumps(
        {
            "package": {"ecosystem": package["ecosystem"], "name": package["name"]},
            "version": package["version"],
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        OSV_QUERY_URL,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read(MAX_RESPONSE_BYTES).decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return [], f"{type(exc).__name__}: {exc}"
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return [], f"malformed OSV response: {exc}"
    if not isinstance(payload, dict):
        return [], "OSV response was not an object"
    return _normalize(payload.get("vulns")), None


def scan(request: dict[str, Any]) -> dict[str, Any]:
    """Return one vulnerability-report/v1 for every requested package version."""

    request_id = _text(request.get("request_id"), "request_id", 128)
    packages = _packages(request)
    replay = request.get("replay")
    replayed = replay.get("osv") if isinstance(replay, dict) else None

    results: list[dict[str, Any]] = []
    errors: list[str] = []
    for package in packages:
        key = f"{package['ecosystem']}/{package['name']}@{package['version']}"
        if isinstance(replayed, dict):
            if key not in replayed:
                errors.append(f"replay has no entry for {key}")
                vulnerabilities, error = [], f"missing replay entry for {key}"
            else:
                vulnerabilities, error = _normalize(replayed[key]), None
        else:
            vulnerabilities, error = _query_osv(package)
            if error:
                errors.append(f"{key}: {error}")
        results.append({**package, "vulnerabilities": vulnerabilities, "error": error})

    total = sum(len(item["vulnerabilities"]) for item in results)
    return {
        "schema_version": "1",
        "request_id": request_id,
        "status": "ok" if not errors else "partial",
        "source": "replay" if isinstance(replayed, dict) else OSV_QUERY_URL,
        "packages": results,
        "total_vulnerabilities": total,
        # An unreachable database is reported, never silently treated as "no vulnerabilities".
        "unknown": sorted(errors),
    }


def main() -> int:
    try:
        request = json.load(sys.stdin)
        if not isinstance(request, dict):
            raise ContractError("input must be one JSON object")
        report = scan(request)
    except ContractError as exc:
        print(json.dumps({"schema_version": "1", "status": "error", "error": str(exc)}))
        return 1
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(
            json.dumps({"schema_version": "1", "status": "error", "error": f"invalid JSON: {exc}"})
        )
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
