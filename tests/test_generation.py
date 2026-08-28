from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import ValidationError

from blackridge.generation import (
    GeneratedSystemProposal,
    GenerationProposalRejected,
    VerifiedComponent,
    materialize_proposal,
    proposal_sha256,
    propose_gap_system,
)
from blackridge.models import (
    AcceptanceScenario,
    Capability,
    CapabilityResult,
    DiscoveryRun,
    SearchQuery,
    SystemRequest,
)
from blackridge.operator import AgentCompletion, AgentUsage

CAPABILITY = Capability(
    id="duplicate-detection",
    description="Detect duplicate files without changing user input data.",
    accepts=["directory/v1"],
    produces=["duplicate-report/v1"],
    searches=[SearchQuery(keywords=["duplicate", "files"])],
    acceptance=[
        AcceptanceScenario(
            id="detect-duplicates",
            description="Report files with identical bytes as one duplicate group.",
            given="Two files contain the same bytes.",
            when="The duplicate finder scans their directory.",
            then=["Both paths occur in one duplicate group."],
        )
    ],
)
REQUEST = SystemRequest(
    name="duplicate-finder",
    goal="Build a deterministic duplicate finder without modifying user input data.",
    capabilities=[CAPABILITY],
)
DISCOVERY = DiscoveryRun(
    created_at=datetime(2026, 8, 28, tzinfo=UTC),
    provider="fixture-discovery/1",
    request=REQUEST,
    results=[CapabilityResult(capability=CAPABILITY, candidates=[])],
)


class _Backend:
    identity = "fixture:operator"

    def complete_json(self, **_: object) -> AgentCompletion:
        content = {
            "schema_version": "1",
            "files": [
                {"path": "dupfinder.py", "content": "print('proposal')\n"},
                {"path": "tests/test_dupfinder.py", "content": "# executable fixture\n"},
            ],
            "run_command": ["python", "dupfinder.py"],
            "component_decisions": [
                {
                    "capability_id": "duplicate-detection",
                    "source": "generated-gap",
                    "identity": "generated:duplicate-detection",
                    "immutable_revision": None,
                    "evidence_level": 0,
                    "rationale": (
                        "No supplied verified component satisfies the complete fixture contract."
                    ),
                }
            ],
            "tests": ["python -m unittest -v"],
            "acceptance_coverage": [
                {
                    "acceptance_id": "detect-duplicates",
                    "test_file": "tests/test_dupfinder.py",
                    "test_name": "test_detect_duplicates",
                    "rationale": (
                        "The executable test checks the required duplicate-report behavior."
                    ),
                }
            ],
            "limitations": ["The proposal has not been executed."],
        }
        return AgentCompletion(
            provider="fixture",
            model="fixed",
            finish_reason="stop",
            content=content,
            content_sha256="0" * 64,
            usage=AgentUsage(),
        )


def test_gap_proposal_is_hash_bound_and_materialized_only_after_approval(
    tmp_path: Path,
) -> None:
    proposal, record = propose_gap_system(
        "Build a deterministic duplicate finder that never modifies input files.",
        request=REQUEST,
        discovery=DISCOVERY,
        backend=_Backend(),
    )
    digest = proposal_sha256(proposal)
    assert record.proposal_sha256 == digest
    with pytest.raises(ValueError, match="differs from"):
        materialize_proposal(proposal, tmp_path / "rejected", approved_sha256="0" * 64)

    hashes = materialize_proposal(
        proposal,
        tmp_path / "accepted",
        approved_sha256=digest,
    )
    assert set(hashes) == {"dupfinder.py", "tests/test_dupfinder.py"}
    assert (tmp_path / "accepted" / "dupfinder.py").read_text(encoding="utf-8") == (
        "print('proposal')\n"
    )


@pytest.mark.parametrize(
    "paths",
    [
        ["/tmp/absolute.py"],
        ["C:\\drive-relative.py"],
        ["../escape.py"],
        ["CON.py"],
        ["stream.py:payload"],
        ["A.py", "a.py"],
    ],
)
def test_generated_proposal_rejects_cross_platform_unsafe_paths(paths: list[str]) -> None:
    files = [{"path": path, "content": "pass\n"} for path in paths]
    with pytest.raises(ValidationError):
        GeneratedSystemProposal.model_validate(
            {
                "files": files,
                "run_command": ["python", "program.py"],
                "component_decisions": [
                    {
                        "capability_id": "duplicate-detection",
                        "source": "generated-gap",
                        "identity": "generated:duplicate-detection",
                        "evidence_level": 0,
                        "rationale": "The fixture requires a generated gap for this capability.",
                    }
                ],
                "tests": ["python -m unittest -v"],
                "acceptance_coverage": [
                    {
                        "acceptance_id": "detect-duplicates",
                        "test_file": paths[0],
                        "test_name": "test_detect_duplicates",
                        "rationale": "The executable test checks the duplicate-report behavior.",
                    }
                ],
            }
        )


def test_generated_proposal_cannot_promote_an_l0_candidate_to_verified() -> None:
    completion = _Backend().complete_json()
    decision = completion.content["component_decisions"][0]
    decision.update(
        {
            "source": "verified-component",
            "identity": "example/unverified",
            "immutable_revision": "a" * 40,
            "evidence_level": 2,
        }
    )

    class _OverclaimingBackend(_Backend):
        def complete_json(self, **_: object) -> AgentCompletion:
            return completion

    with pytest.raises(ValueError, match="overclaims supplied component"):
        propose_gap_system(
            "Build a deterministic duplicate finder that never modifies input files.",
            request=REQUEST,
            discovery=DISCOVERY,
            backend=_OverclaimingBackend(),
        )


def test_standard_library_claim_requires_matching_supplied_evidence() -> None:
    completion = _Backend().complete_json()
    decision = completion.content["component_decisions"][0]
    decision.update(
        {
            "source": "standard-library",
            "identity": "python-standard-library",
            "immutable_revision": "cpython-3.12.10",
            "evidence_level": 2,
        }
    )

    class _StandardLibraryBackend(_Backend):
        def complete_json(self, **_: object) -> AgentCompletion:
            return completion

    with pytest.raises(ValueError, match="overclaims supplied component"):
        propose_gap_system(
            "Build a deterministic duplicate finder that never modifies input files.",
            request=REQUEST,
            discovery=DISCOVERY,
            backend=_StandardLibraryBackend(),
        )

    proposal, _ = propose_gap_system(
        "Build a deterministic duplicate finder that never modifies input files.",
        request=REQUEST,
        discovery=DISCOVERY,
        backend=_StandardLibraryBackend(),
        verified_components=[
            VerifiedComponent(
                capability_id="duplicate-detection",
                identity="python-standard-library",
                immutable_revision="cpython-3.12.10",
                evidence_level=2,
            )
        ],
    )
    assert proposal.component_decisions[0].source == "standard-library"


def test_review_feedback_is_bounded_and_hash_bound() -> None:
    feedback = "Reject the prior proposal because its output collision destroys input bytes."
    _, record = propose_gap_system(
        "Build a deterministic duplicate finder that never modifies input files.",
        request=REQUEST,
        discovery=DISCOVERY,
        backend=_Backend(),
        review_feedback=feedback,
    )
    assert record.review_feedback_sha256 == sha256(feedback.encode()).hexdigest()

    with pytest.raises(ValueError, match="50-kilobyte"):
        propose_gap_system(
            "Build a deterministic duplicate finder that never modifies input files.",
            request=REQUEST,
            discovery=DISCOVERY,
            backend=_Backend(),
            review_feedback="x" * 50_001,
        )


def test_schema_rejected_provider_completion_is_retained_as_evidence() -> None:
    completion = _Backend().complete_json()
    completion.content["files"][0]["forbidden"] = True

    class _InvalidBackend(_Backend):
        def complete_json(self, **_: object) -> AgentCompletion:
            return completion

    with pytest.raises(GenerationProposalRejected) as caught:
        propose_gap_system(
            "Build a deterministic duplicate finder that never modifies input files.",
            request=REQUEST,
            discovery=DISCOVERY,
            backend=_InvalidBackend(),
        )
    record = caught.value.record
    assert record.proposal_status == "schema-rejected"
    assert record.completion.content["files"][0]["forbidden"] is True
    assert record.validation_errors[0]["type"] == "extra_forbidden"


def test_acceptance_coverage_must_match_request_exactly() -> None:
    completion = _Backend().complete_json()
    completion.content["acceptance_coverage"][0]["acceptance_id"] = "invented-check"

    class _InventedCoverageBackend(_Backend):
        def complete_json(self, **_: object) -> AgentCompletion:
            return completion

    with pytest.raises(ValueError, match="acceptance coverage does not cover"):
        propose_gap_system(
            "Build a deterministic duplicate finder that never modifies input files.",
            request=REQUEST,
            discovery=DISCOVERY,
            backend=_InventedCoverageBackend(),
        )


def test_acceptance_coverage_must_reference_a_generated_test_file() -> None:
    completion = _Backend().complete_json()
    completion.content["acceptance_coverage"][0]["test_file"] = "tests/missing.py"

    class _MissingTestBackend(_Backend):
        def complete_json(self, **_: object) -> AgentCompletion:
            return completion

    with pytest.raises(GenerationProposalRejected) as caught:
        propose_gap_system(
            "Build a deterministic duplicate finder that never modifies input files.",
            request=REQUEST,
            discovery=DISCOVERY,
            backend=_MissingTestBackend(),
        )
    assert caught.value.record.validation_errors[0]["type"] == "value_error"
