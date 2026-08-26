from __future__ import annotations

from datetime import UTC, datetime, timedelta

from blackridge.models import RepositoryMetadata, SearchQuery
from blackridge.ranking import rank_candidate

NOW = datetime(2026, 8, 26, tzinfo=UTC)
QUERY = SearchQuery(keywords=["PDF", "parser"])


def metadata(**updates: object) -> RepositoryMetadata:
    values: dict[str, object] = {
        "full_name": "example/parser",
        "url": "https://github.com/example/parser",
        "stars": 5000,
        "forks": 500,
        "open_issues": 25,
        "pushed_at": NOW - timedelta(days=10),
        "license_spdx": "Apache-2.0",
        "security_score": 8.5,
    }
    values.update(updates)
    return RepositoryMetadata.model_validate(values)


def test_active_permissive_candidate_is_eligible_but_not_selection_ready() -> None:
    candidate = rank_candidate(
        metadata(), capability_id="document-parser", query=QUERY, position=1, now=NOW
    )

    assert candidate.decision == "eligible-for-inspection"
    assert candidate.selection_ready is False
    assert candidate.score.total > 80
    assert not candidate.blockers


def test_archived_candidate_is_rejected_even_with_a_high_score() -> None:
    candidate = rank_candidate(
        metadata(archived=True),
        capability_id="document-parser",
        query=QUERY,
        position=1,
        now=NOW,
    )

    assert candidate.decision == "rejected"
    assert "repository is archived" in candidate.blockers


def test_unknown_license_requires_manual_review() -> None:
    candidate = rank_candidate(
        metadata(license_spdx=None),
        capability_id="document-parser",
        query=QUERY,
        position=1,
        now=NOW,
    )

    assert candidate.decision == "manual-review"
    assert candidate.score.license_confidence == 0

