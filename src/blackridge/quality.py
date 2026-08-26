"""Adapters for upstream quality signals."""

from __future__ import annotations

from collections.abc import Callable

import httpx


class OpenSSFScorecardClient:
    """Reads the public OpenSSF Scorecard API; absence is represented as unknown."""

    def __init__(
        self,
        *,
        fetch: Callable[[str], float | None] | None = None,
        timeout_seconds: float = 10,
    ) -> None:
        self._fetch = fetch
        self.timeout_seconds = timeout_seconds

    def score(self, full_name: str) -> float | None:
        if self._fetch:
            return self._fetch(full_name)
        url = f"https://api.securityscorecards.dev/projects/github.com/{full_name}"
        try:
            response = httpx.get(url, timeout=self.timeout_seconds, follow_redirects=True)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            value = response.json().get("score")
            return float(value) if value is not None else None
        except (httpx.HTTPError, ValueError, TypeError):
            return None
