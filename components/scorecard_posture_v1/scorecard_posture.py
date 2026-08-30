#!/usr/bin/env python3
"""Fetch an independent OpenSSF Scorecard assessment for one repository.

Reads one `audit-request/v1` object on stdin and writes one `security-posture/v1` object on
stdout. The assessment comes from the public OpenSSF Scorecard API, which publishes results
for repositories it has already scanned.

An absent scorecard is reported as `unavailable`, never as a passing score. A `replay` block
supplies a recorded response instead of calling the network.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from typing import Any

SCORECARD_URL = "https://api.securityscorecards.dev/projects/github.com/{repository}"
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
TIMEOUT_SECONDS = 20


class ContractError(ValueError):
    """The caller supplied a request outside the frozen component contract."""


def _repository(request: dict[str, Any]) -> str:
    value = request.get("repository")
    if not isinstance(value, str) or not REPOSITORY_PATTERN.match(value):
        raise ContractError("repository must be 'owner/name'")
    return value


def _checks(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    checks = []
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        score = item.get("score")
        checks.append(
            {
                "name": item["name"],
                "score": score if isinstance(score, int | float) else None,
                "reason": item.get("reason") if isinstance(item.get("reason"), str) else None,
            }
        )
    return sorted(checks, key=lambda item: item["name"])


def _fetch(repository: str) -> tuple[dict[str, Any] | None, str | None]:
    url = SCORECARD_URL.format(repository=repository)
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read(MAX_RESPONSE_BYTES).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None, "no published scorecard for this repository"
        return None, f"HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return None, f"malformed scorecard response: {exc}"
    if not isinstance(payload, dict):
        return None, "scorecard response was not an object"
    return payload, None


def assess(request: dict[str, Any]) -> dict[str, Any]:
    """Return one security-posture/v1 observation, or an explicit unavailable status."""

    request_id = request.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        raise ContractError("request_id must be a non-empty string")
    repository = _repository(request)
    replay = request.get("replay")
    replayed = replay.get("scorecard") if isinstance(replay, dict) else None

    if isinstance(replayed, dict):
        payload, error = replayed, None
        source = "replay"
    else:
        payload, error = _fetch(repository)
        source = SCORECARD_URL.format(repository=repository)

    if payload is None:
        return {
            "schema_version": "1",
            "request_id": request_id,
            "repository": repository,
            "status": "unavailable",
            "score": None,
            "checks": [],
            "source": source,
            "error": error,
        }

    score = payload.get("score")
    return {
        "schema_version": "1",
        "request_id": request_id,
        "repository": repository,
        "status": "ok",
        "score": score if isinstance(score, int | float) else None,
        "checks": _checks(payload.get("checks")),
        "source": source,
        "error": None,
    }


def main() -> int:
    try:
        request = json.load(sys.stdin)
        if not isinstance(request, dict):
            raise ContractError("input must be one JSON object")
        posture = assess(request)
    except ContractError as exc:
        print(json.dumps({"schema_version": "1", "status": "error", "error": str(exc)}))
        return 1
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(
            json.dumps({"schema_version": "1", "status": "error", "error": f"invalid JSON: {exc}"})
        )
        return 1
    print(json.dumps(posture, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
