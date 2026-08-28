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
    compose_with_locked_files,
    materialize_proposal,
    normalize_completion_content,
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


def test_public_evaluator_contract_is_bounded_hash_bound_and_in_prompt() -> None:
    class _PromptBackend(_Backend):
        user = ""

        def complete_json(self, **kwargs: object) -> AgentCompletion:
            self.user = str(kwargs["user"])
            return super().complete_json()

    evaluator = "def test_public_contract():\n    assert True\n"
    backend = _PromptBackend()
    _, record = propose_gap_system(
        "Build a deterministic duplicate finder that never modifies input files.",
        request=REQUEST,
        discovery=DISCOVERY,
        backend=backend,
        public_evaluator_contract=evaluator,
    )

    assert record.public_evaluator_sha256 == sha256(evaluator.encode()).hexdigest()
    assert "KNOWN PUBLIC EVALUATOR CONTRACT" in backend.user
    assert evaluator in backend.user

    with pytest.raises(ValueError, match="200-kilobyte"):
        propose_gap_system(
            "Build a deterministic duplicate finder that never modifies input files.",
            request=REQUEST,
            discovery=DISCOVERY,
            backend=_Backend(),
            public_evaluator_contract="x" * 200_001,
        )


def test_schema_rejected_provider_completion_is_retained_as_evidence() -> None:
    completion = _Backend().complete_json()
    completion.content["files"][0]["forbidden"] = True
    completion.content["files"][0]["path"] = 7

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
    assert record.ignored_provider_fields == ["files[0].forbidden"]
    assert record.validation_errors[0]["type"] == "string_type"


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


def test_benign_provider_metadata_is_ignored_but_retained_as_an_audit_path() -> None:
    completion = _Backend().complete_json()
    completion.content["files"][0]["executable"] = True

    class _MetadataBackend(_Backend):
        def complete_json(self, **_: object) -> AgentCompletion:
            return completion

    proposal, record = propose_gap_system(
        "Build a deterministic duplicate finder that never modifies input files.",
        request=REQUEST,
        discovery=DISCOVERY,
        backend=_MetadataBackend(),
    )
    assert proposal.files[0].path == "dupfinder.py"
    assert record.ignored_provider_fields == ["files[0].executable"]
    assert record.completion.content["files"][0]["executable"] is True


def test_normalization_keeps_wrong_types_for_strict_schema_rejection() -> None:
    normalized, ignored = normalize_completion_content({"files": "not-a-list", "note": True})
    assert normalized == {"files": "not-a-list"}
    assert ignored == ["note"]


def test_composition_keeps_passing_program_bytes_and_accepts_repaired_tests() -> None:
    prior, _ = propose_gap_system(
        "Build a deterministic duplicate finder that never modifies input files.",
        request=REQUEST,
        discovery=DISCOVERY,
        backend=_Backend(),
    )
    next_value = prior.model_dump(mode="json")
    next_value["files"][0]["content"] = "print('regressed program')\n"
    next_value["files"][1]["content"] = "# repaired executable tests\n"
    next_proposal = GeneratedSystemProposal.model_validate(next_value)

    composed, record = compose_with_locked_files(
        prior, next_proposal, locked_paths=["dupfinder.py"]
    )

    assert composed.files[0].content == "print('proposal')\n"
    assert composed.files[1].content == "# repaired executable tests\n"
    assert record.prior_proposal_sha256 == proposal_sha256(prior)
    assert record.next_proposal_sha256 == proposal_sha256(next_proposal)
    assert record.composed_proposal_sha256 == proposal_sha256(composed)
    assert set(record.locked_file_sha256) == {"dupfinder.py"}


def test_composition_rejects_a_locked_file_removed_by_next_proposal() -> None:
    prior, _ = propose_gap_system(
        "Build a deterministic duplicate finder that never modifies input files.",
        request=REQUEST,
        discovery=DISCOVERY,
        backend=_Backend(),
    )
    value = prior.model_dump(mode="json")
    value["files"] = value["files"][1:]
    value["acceptance_coverage"][0]["test_file"] = "tests/test_dupfinder.py"
    next_proposal = GeneratedSystemProposal.model_validate(value)

    with pytest.raises(ValueError, match="must exist in both proposals"):
        compose_with_locked_files(prior, next_proposal, locked_paths=["dupfinder.py"])


def test_generation_prompt_requires_black_box_portable_tests() -> None:
    class _PromptBackend(_Backend):
        user = ""

        def complete_json(self, **kwargs: object) -> AgentCompletion:
            self.user = str(kwargs["user"])
            return super().complete_json()

    backend = _PromptBackend()
    propose_gap_system(
        "Build a deterministic duplicate finder that never modifies input files.",
        request=REQUEST,
        discovery=DISCOVERY,
        backend=backend,
    )

    assert "only the public run_command/CLI contract" in backend.user
    assert "Do not import the generated program module" in backend.user
    assert "Resolve the program to an absolute path" in backend.user
