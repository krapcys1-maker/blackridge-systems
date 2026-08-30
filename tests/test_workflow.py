from __future__ import annotations

import time
from datetime import UTC, datetime

from blackridge.errors import ExternalToolError
from blackridge.models import Capability, RepositoryMetadata, SearchQuery, SystemRequest
from blackridge.octocode import DiscoveryHit
from blackridge.quality import ScorecardObservation
from blackridge.workflow import discover

NOW = datetime(2026, 8, 27, tzinfo=UTC)
QUERY = SearchQuery(keywords=["research", "evidence"])
CAPABILITY = Capability(
    id="grounded-research",
    description="Produce grounded research with exact evidence.",
    searches=[QUERY],
)
REQUEST = SystemRequest(
    name="research-system",
    goal="Build a grounded research system with inspectable evidence.",
    capabilities=[CAPABILITY],
)


def _metadata(name: str) -> RepositoryMetadata:
    return RepositoryMetadata(
        full_name=name,
        url=f"https://github.com/{name}",
        description="research evidence",
        stars=100,
        forks=10,
        pushed_at=NOW,
        license_spdx="Apache-2.0",
    )


class _Discovery:
    provider_name = "fixture-discovery/1"

    def __init__(self, hits: list[DiscoveryHit]) -> None:
        self.hits = hits
        self.limits: list[int] = []

    def search(self, _capability: Capability, *, limit: int) -> list[DiscoveryHit]:
        self.limits.append(limit)
        return self.hits


class _DelayedGitHub:
    def enrich(self, metadata: RepositoryMetadata) -> RepositoryMetadata:
        if metadata.full_name.startswith("alpha/"):
            time.sleep(0.03)
        return metadata


class _UnavailableScorecard:
    def inspect(self, full_name: str) -> ScorecardObservation:
        return ScorecardObservation(None, "not-found", f"no score for {full_name}")


def test_discovery_restores_deterministic_order_after_concurrent_enrichment() -> None:
    # Beta completes first, but equal scores must use the repository-name tie-break.
    hits = [
        DiscoveryHit(_metadata("beta/project"), QUERY, 1),
        DiscoveryHit(_metadata("alpha/project"), QUERY, 1),
    ]
    provider = _Discovery(hits)

    first = discover(
        REQUEST,
        discovery=provider,  # type: ignore[arg-type]
        github=_DelayedGitHub(),  # type: ignore[arg-type]
        scorecard=_UnavailableScorecard(),  # type: ignore[arg-type]
        workers=2,
        limit=4,
        now=NOW,
    )
    second = discover(
        REQUEST,
        discovery=provider,  # type: ignore[arg-type]
        github=_DelayedGitHub(),  # type: ignore[arg-type]
        scorecard=_UnavailableScorecard(),  # type: ignore[arg-type]
        workers=2,
        limit=4,
        now=NOW,
    )

    expected_names = ["alpha/project", "beta/project"]
    assert [item.metadata.full_name for item in first.results[0].candidates] == expected_names
    assert first.model_dump() == second.model_dump()
    assert first.warnings == [
        "OpenSSF Scorecard not-found for alpha/project: no score for alpha/project",
        "OpenSSF Scorecard not-found for beta/project: no score for beta/project",
    ]
    assert provider.limits == [4, 4]


class _PartiallyFailingGitHub:
    def enrich(self, metadata: RepositoryMetadata) -> RepositoryMetadata:
        if metadata.full_name == "broken/project":
            raise ExternalToolError("fixture API unavailable")
        return metadata.model_copy(update={"stars": 250})


class _AvailableScorecard:
    def inspect(self, _full_name: str) -> ScorecardObservation:
        return ScorecardObservation(8.5, "available", "fixture score")


def test_github_failure_keeps_the_candidate_and_records_a_warning() -> None:
    hits = [DiscoveryHit(_metadata("broken/project"), QUERY, 1)]

    result = discover(
        REQUEST,
        discovery=_Discovery(hits),  # type: ignore[arg-type]
        github=_PartiallyFailingGitHub(),  # type: ignore[arg-type]
        scorecard=_AvailableScorecard(),  # type: ignore[arg-type]
        workers=1,
        now=NOW,
    )

    candidate = result.results[0].candidates[0]
    assert candidate.metadata.full_name == "broken/project"
    assert candidate.metadata.security_score == 8.5
    assert result.warnings == [
        "GitHub enrichment failed for broken/project: fixture API unavailable"
    ]


def test_empty_discovery_result_remains_a_valid_capability_result() -> None:
    result = discover(
        REQUEST,
        discovery=_Discovery([]),  # type: ignore[arg-type]
        github=_DelayedGitHub(),  # type: ignore[arg-type]
        scorecard=_AvailableScorecard(),  # type: ignore[arg-type]
        workers=0,
        now=NOW,
    )

    assert result.provider == "fixture-discovery/1"
    assert result.created_at == NOW
    assert result.results[0].capability == CAPABILITY
    assert result.results[0].candidates == []
    assert result.warnings == []


def test_denied_repository_is_removed_before_enrichment() -> None:
    hits = [
        DiscoveryHit(_metadata("blocked/project"), QUERY, 1),
        DiscoveryHit(_metadata("allowed/project"), QUERY, 2),
    ]
    result = discover(
        REQUEST,
        discovery=_Discovery(hits),  # type: ignore[arg-type]
        github=_DelayedGitHub(),  # type: ignore[arg-type]
        scorecard=_AvailableScorecard(),  # type: ignore[arg-type]
        denied_repositories={"BLOCKED/PROJECT"},
        workers=1,
        now=NOW,
    )

    assert [candidate.metadata.full_name for candidate in result.results[0].candidates] == [
        "allowed/project"
    ]
    assert result.warnings == [
        "Denied-repository policy excluded 1 candidate(s) for grounded-research"
    ]
