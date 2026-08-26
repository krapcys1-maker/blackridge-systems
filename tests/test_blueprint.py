from __future__ import annotations

from datetime import UTC, datetime

from blackridge.blueprint import build_blueprint
from blackridge.models import (
    Candidate,
    Capability,
    CapabilityResult,
    DiscoveryRun,
    RepositoryMetadata,
    ScoreBreakdown,
    SearchQuery,
    SystemRequest,
)


def test_blueprint_keeps_l0_choice_provisional() -> None:
    query = SearchQuery(keywords=["PDF", "parser"])
    capability = Capability(
        id="document-parser",
        description="Parse documents into structured evidence.",
        accepts=["document/pdf"],
        produces=["evidence-document/v1"],
        searches=[query],
    )
    score = ScoreBreakdown(
        search_fit=25,
        maintenance=20,
        adoption=10,
        community=5,
        issue_health=5,
        license_confidence=15,
        security_posture=20,
        total=100,
    )
    candidate = Candidate(
        capability_id=capability.id,
        metadata=RepositoryMetadata(
            full_name="example/parser",
            url="https://github.com/example/parser",
            license_spdx="MIT",
        ),
        search_query=query,
        search_position=1,
        decision="eligible-for-inspection",
        score=score,
    )
    request = SystemRequest(
        name="test-system",
        goal="Build a test system from reusable upstream components.",
        capabilities=[capability],
    )
    run = DiscoveryRun(
        created_at=datetime(2026, 8, 26, tzinfo=UTC),
        provider="test",
        request=request,
        results=[CapabilityResult(capability=capability, candidates=[candidate])],
    )

    blueprint = build_blueprint(run, now=datetime(2026, 8, 26, tzinfo=UTC))

    assert blueprint.release_ready is False
    assert blueprint.components[0].repository == "example/parser"
    assert blueprint.components[0].alternatives == []
    assert blueprint.components[0].status == "provisional"
    assert "must boot in a sandbox before selection" in blueprint.components[0].warnings
