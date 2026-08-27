"""Transparent metadata triage. Functional verification happens in later evidence levels."""

from __future__ import annotations

import math
from datetime import UTC, datetime

from blackridge.models import (
    Candidate,
    CandidateDecision,
    RepositoryMetadata,
    ScoreBreakdown,
    SearchQuery,
)

PERMISSIVE_LICENSES = {
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "ISC",
    "MIT",
    "MPL-2.0",
    "Unlicense",
}
REVIEW_LICENSES = {
    "AGPL-3.0",
    "BUSL-1.1",
    "Elastic-2.0",
    "GPL-2.0",
    "GPL-3.0",
    "LGPL-2.1",
    "LGPL-3.0",
    "NOASSERTION",
    "SSPL-1.0",
}


def _days_since(value: datetime | None, now: datetime) -> int:
    if value is None:
        return 3650
    aware = value if value.tzinfo else value.replace(tzinfo=UTC)
    return max(0, (now - aware.astimezone(UTC)).days)


def _round(value: float) -> float:
    return round(max(0.0, value), 2)


def _query_coverage(metadata: RepositoryMetadata, query: SearchQuery) -> float:
    searchable = " ".join(
        [
            metadata.full_name,
            metadata.description or "",
            " ".join(metadata.topics),
        ]
    ).lower()
    matches = sum(keyword.lower() in searchable for keyword in query.keywords)
    return matches / len(query.keywords)


def rank_candidate(
    metadata: RepositoryMetadata,
    *,
    capability_id: str,
    query: SearchQuery,
    position: int,
    now: datetime | None = None,
) -> Candidate:
    """Rank one L0 candidate and apply hard metadata gates."""

    current_time = (now or datetime.now(UTC)).astimezone(UTC)
    age_days = _days_since(metadata.pushed_at, current_time)

    position_prior = math.exp(-0.12 * (position - 1))
    search_fit = 25 * (0.55 * _query_coverage(metadata, query) + 0.45 * position_prior)
    maintenance = 20 * math.exp(-age_days / 540)
    adoption = 10 * min(1.0, math.log10(metadata.stars + 1) / 4)
    community = 5 * min(1.0, math.log10(metadata.forks + 1) / 3)

    issue_ratio = metadata.open_issues / max(metadata.stars, 1)
    if issue_ratio <= 0.01:
        issue_health = 5.0
    elif issue_ratio <= 0.05:
        issue_health = 3.5
    elif issue_ratio <= 0.15:
        issue_health = 2.0
    else:
        issue_health = 0.5

    if metadata.license_spdx in PERMISSIVE_LICENSES:
        license_confidence = 15.0
    elif metadata.license_spdx in REVIEW_LICENSES:
        license_confidence = 5.0
    else:
        license_confidence = 0.0

    security_posture = 5.0 if metadata.security_score is None else 20 * metadata.security_score / 10

    values = {
        "search_fit": _round(search_fit),
        "maintenance": _round(maintenance),
        "adoption": _round(adoption),
        "community": _round(community),
        "issue_health": _round(issue_health),
        "license_confidence": _round(license_confidence),
        "security_posture": _round(security_posture),
    }
    breakdown = ScoreBreakdown(**values, total=_round(sum(values.values())))

    warnings: list[str] = []
    blockers: list[str] = []
    decision: CandidateDecision = "eligible-for-inspection"

    if metadata.security_score is None:
        warnings.append("OpenSSF Scorecard is unavailable; security posture is unverified")
    if metadata.is_fork:
        warnings.append("repository is a fork; verify the maintained upstream")
    if age_days > 730:
        warnings.append(f"repository has not been pushed for {age_days} days")
        decision = "manual-review"
    if metadata.license_spdx is None or metadata.license_spdx == "NOASSERTION":
        blockers.append("license is missing or not machine-verifiable")
        decision = "manual-review"
    elif metadata.license_spdx in REVIEW_LICENSES:
        blockers.append(
            f"license requires an explicit integration-mode review: {metadata.license_spdx}"
        )
        decision = "manual-review"
    if metadata.archived:
        blockers.append("repository is archived")
        decision = "rejected"

    return Candidate(
        capability_id=capability_id,
        metadata=metadata,
        search_query=query,
        search_position=position,
        decision=decision,
        selection_ready=False,
        score=breakdown,
        warnings=warnings,
        blockers=blockers,
    )
