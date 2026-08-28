"""Adapters for upstream quality signals."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class ScorecardObservation:
    score: float | None
    status: str
    detail: str


class OpenSSFScorecardClient:
    """Reads OpenSSF Scorecard while retaining why a score is unavailable."""

    def __init__(
        self,
        *,
        fetch: Callable[[str], float | None] | None = None,
        timeout_seconds: float = 10,
    ) -> None:
        self._fetch = fetch
        self.timeout_seconds = timeout_seconds

    def score(self, full_name: str) -> float | None:
        """Compatibility helper for callers that only need the numeric value."""

        return self.inspect(full_name).score

    def inspect(self, full_name: str) -> ScorecardObservation:
        if self._fetch:
            score = self._fetch(full_name)
            if score is None:
                return ScorecardObservation(
                    score=None,
                    status="not-found",
                    detail="injected provider returned no Scorecard",
                )
            if not 0 <= score <= 10:
                return ScorecardObservation(
                    score=None,
                    status="invalid-response",
                    detail="injected provider returned a score outside 0..10",
                )
            return ScorecardObservation(score=score, status="available", detail="score returned")
        url = f"https://api.securityscorecards.dev/projects/github.com/{full_name}"
        try:
            response = httpx.get(url, timeout=self.timeout_seconds, follow_redirects=True)
            if response.status_code == 404:
                return ScorecardObservation(
                    score=None,
                    status="not-found",
                    detail="OpenSSF has no Scorecard for this repository",
                )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise TypeError("Scorecard response is not an object")
            value = payload.get("score")
            if value is None:
                return ScorecardObservation(
                    score=None,
                    status="invalid-response",
                    detail="OpenSSF response did not contain a score",
                )
            score = float(value)
            if not 0 <= score <= 10:
                return ScorecardObservation(
                    score=None,
                    status="invalid-response",
                    detail="OpenSSF response score is outside 0..10",
                )
            return ScorecardObservation(score=score, status="available", detail="score returned")
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            return ScorecardObservation(
                score=None,
                status="error",
                detail=f"OpenSSF request failed: {type(exc).__name__}: {exc}",
            )
