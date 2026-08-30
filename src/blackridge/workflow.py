"""Deterministic discovery workflow composed from upstream adapters."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from typing import Protocol

from blackridge.errors import BlackridgeError
from blackridge.github import GitHubCli
from blackridge.models import (
    Capability,
    CapabilityResult,
    DiscoveryRun,
    RepositoryMetadata,
    SystemRequest,
)
from blackridge.octocode import DiscoveryHit
from blackridge.quality import OpenSSFScorecardClient
from blackridge.ranking import rank_candidate


class DiscoveryProvider(Protocol):
    """Read-only repository discovery boundary shared by Octocode and GitHub CLI."""

    @property
    def provider_name(self) -> str: ...

    def search(self, capability: Capability, *, limit: int = 10) -> list[DiscoveryHit]: ...


def _enrich_one(
    hit: DiscoveryHit,
    github: GitHubCli,
    scorecard: OpenSSFScorecardClient,
) -> tuple[DiscoveryHit, RepositoryMetadata, list[str]]:
    warnings: list[str] = []
    metadata = hit.metadata
    try:
        metadata = github.enrich(metadata)
    except BlackridgeError as exc:
        warnings.append(f"GitHub enrichment failed for {metadata.full_name}: {exc}")
    security = scorecard.inspect(metadata.full_name)
    metadata = RepositoryMetadata.model_validate(
        {**metadata.model_dump(), "security_score": security.score}
    )
    if security.status != "available":
        warnings.append(
            f"OpenSSF Scorecard {security.status} for {metadata.full_name}: {security.detail}"
        )
    return hit, metadata, warnings


def discover(
    request: SystemRequest,
    *,
    discovery: DiscoveryProvider,
    github: GitHubCli,
    scorecard: OpenSSFScorecardClient,
    limit: int = 10,
    workers: int = 8,
    now: datetime | None = None,
    denied_repositories: set[str] | None = None,
) -> DiscoveryRun:
    """Search, enrich, gate, and rank candidates for every capability."""

    current_time = (now or datetime.now(UTC)).astimezone(UTC)
    results: list[CapabilityResult] = []
    run_warnings: list[str] = []
    denied = {repository.casefold() for repository in (denied_repositories or set())}

    for capability in request.capabilities:
        hits = discovery.search(capability, limit=limit)
        blocked = [hit for hit in hits if hit.metadata.full_name.casefold() in denied]
        if blocked:
            run_warnings.append(
                f"Denied-repository policy excluded {len(blocked)} candidate(s) for {capability.id}"
            )
            hits = [hit for hit in hits if hit.metadata.full_name.casefold() not in denied]
        enriched: list[tuple[DiscoveryHit, RepositoryMetadata, list[str]]] = []
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(hits) or 1))) as pool:
            futures = {pool.submit(_enrich_one, hit, github, scorecard): hit for hit in hits}
            for future in as_completed(futures):
                hit, metadata, warnings = future.result()
                enriched.append((hit, metadata, warnings))

        # Completion order is intentionally nondeterministic. Restore provider order with a
        # stable repository tie-break before emitting candidates or warnings.
        enriched.sort(key=lambda item: (item[0].position, item[1].full_name.casefold()))
        for _, _, warnings in enriched:
            run_warnings.extend(warnings)

        candidates = [
            rank_candidate(
                metadata,
                capability_id=capability.id,
                query=hit.query,
                position=hit.position,
                now=current_time,
            )
            for hit, metadata, _ in enriched
        ]
        candidates.sort(
            key=lambda candidate: (-candidate.score.total, candidate.metadata.full_name.casefold())
        )
        results.append(CapabilityResult(capability=capability, candidates=candidates))

    if getattr(discovery, "budget_exhausted", False):
        run_warnings.append(
            "GitHub search query budget was exhausted; retained results are explicitly partial"
        )

    return DiscoveryRun(
        created_at=current_time,
        provider=discovery.provider_name,
        request=request,
        results=results,
        warnings=run_warnings,
    )
