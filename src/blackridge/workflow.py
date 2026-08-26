"""Deterministic discovery workflow composed from upstream adapters."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime

from blackridge.errors import BlackridgeError
from blackridge.github import GitHubCli
from blackridge.models import CapabilityResult, DiscoveryRun, RepositoryMetadata, SystemRequest
from blackridge.octocode import DiscoveryHit, OctocodeDiscovery
from blackridge.quality import OpenSSFScorecardClient
from blackridge.ranking import rank_candidate


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
    metadata = metadata.model_copy(update={"security_score": security.score})
    if security.status != "available":
        warnings.append(
            f"OpenSSF Scorecard {security.status} for {metadata.full_name}: {security.detail}"
        )
    return hit, metadata, warnings


def discover(
    request: SystemRequest,
    *,
    discovery: OctocodeDiscovery,
    github: GitHubCli,
    scorecard: OpenSSFScorecardClient,
    limit: int = 10,
    workers: int = 8,
    now: datetime | None = None,
) -> DiscoveryRun:
    """Search, enrich, gate, and rank candidates for every capability."""

    current_time = (now or datetime.now(UTC)).astimezone(UTC)
    results: list[CapabilityResult] = []
    run_warnings: list[str] = []

    for capability in request.capabilities:
        hits = discovery.search(capability, limit=limit)
        enriched: list[tuple[DiscoveryHit, RepositoryMetadata]] = []
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(hits) or 1))) as pool:
            futures = {pool.submit(_enrich_one, hit, github, scorecard): hit for hit in hits}
            for future in as_completed(futures):
                hit, metadata, warnings = future.result()
                enriched.append((hit, metadata))
                run_warnings.extend(warnings)

        candidates = [
            rank_candidate(
                metadata,
                capability_id=capability.id,
                query=hit.query,
                position=hit.position,
                now=current_time,
            )
            for hit, metadata in enriched
        ]
        candidates.sort(key=lambda candidate: candidate.score.total, reverse=True)
        results.append(CapabilityResult(capability=capability, candidates=candidates))

    return DiscoveryRun(
        created_at=current_time,
        provider=discovery.provider_name,
        request=request,
        results=results,
        warnings=run_warnings,
    )
