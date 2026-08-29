from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import ValidationError

from blackridge.generation import (
    GeneratedFile,
    GeneratedSystemProposal,
    GenerationProposalRejected,
    VerifiedComponent,
    compose_with_locked_files,
    is_generated_test_path,
    materialize_proposal,
    normalize_completion_content,
    proposal_sha256,
    propose_gap_system,
    propose_test_only_repair,
)
from blackridge.generation import (
    TestRepairProposalRejected as RepairProposalRejected,
)
from blackridge.generation import (
    test_suite_sha256 as _test_suite_sha256,
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
TEST_SOURCE = "\n".join(
    ["def test_detect_duplicates():\n    assert 1 == 1"]
    + [f"def test_case_{number}():\n    assert True" for number in range(1, 9)]
)


class _Backend:
    identity = "fixture:operator"

    def complete_json(self, **_: object) -> AgentCompletion:
        content = {
            "schema_version": "1",
            "files": [
                {"path": "dupfinder.py", "content": "print('proposal')\n"},
                {
                    "path": "tests/test_dupfinder.py",
                    "content": TEST_SOURCE,
                },
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
    record = caught.value.record
    assert record.validation_errors[0]["type"] == "value_error"
    serialized = json.loads(record.model_dump_json())
    assert serialized["validation_errors"][0]["ctx"]["error"].startswith(
        "acceptance evidence references"
    )


def test_acceptance_coverage_must_reference_a_concrete_test_function() -> None:
    completion = _Backend().complete_json()
    completion.content["acceptance_coverage"][0]["test_name"] = "test_missing"

    class _MissingFunctionBackend(_Backend):
        def complete_json(self, **_: object) -> AgentCompletion:
            return completion

    with pytest.raises(GenerationProposalRejected) as caught:
        propose_gap_system(
            "Build a deterministic duplicate finder that never modifies input files.",
            request=REQUEST,
            discovery=DISCOVERY,
            backend=_MissingFunctionBackend(),
        )
    serialized = json.loads(caught.value.record.model_dump_json())
    assert serialized["validation_errors"][0]["type"] == "value_error"
    assert "missing concrete test" in serialized["validation_errors"][0]["ctx"]["error"]


def test_generated_suite_requires_nine_concrete_test_functions() -> None:
    completion = _Backend().complete_json()
    completion.content["files"][1]["content"] = "def test_detect_duplicates():\n    assert True\n"

    class _TooSmallSuiteBackend(_Backend):
        def complete_json(self, **_: object) -> AgentCompletion:
            return completion

    with pytest.raises(GenerationProposalRejected) as caught:
        propose_gap_system(
            "Build a deterministic duplicate finder that never modifies input files.",
            request=REQUEST,
            discovery=DISCOVERY,
            backend=_TooSmallSuiteBackend(),
        )
    assert "fewer than 9" in caught.value.record.validation_errors[0]["ctx"]["error"]


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
    next_value["files"][1]["content"] = TEST_SOURCE.replace("1 == 1", "2 == 2")
    next_proposal = GeneratedSystemProposal.model_validate(next_value)

    composed, record = compose_with_locked_files(
        prior, next_proposal, locked_paths=["dupfinder.py"]
    )

    assert composed.files[0].content == "print('proposal')\n"
    assert composed.files[1].content == TEST_SOURCE.replace("1 == 1", "2 == 2")
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


def test_test_only_repair_keeps_product_and_control_fields_byte_exact() -> None:
    prior, _ = propose_gap_system(
        "Build a deterministic duplicate finder that never modifies input files.",
        request=REQUEST,
        discovery=DISCOVERY,
        backend=_Backend(),
    )
    completion = _Backend().complete_json()
    completion.content = {
        "schema_version": "1",
        "files": [
            {
                "path": "tests/test_dupfinder.py",
                "content": TEST_SOURCE.replace("1 == 1", "3 == 3"),
            }
        ],
        "acceptance_coverage": [
            {
                "acceptance_id": "detect-duplicates",
                "test_file": "tests/test_dupfinder.py",
                "test_name": "test_detect_duplicates",
                "rationale": "The black-box fixture checks that input bytes remain unchanged.",
            }
        ],
        "limitations": ["Sandbox execution remains required."],
        "provider_note": "ignored but retained in the audit path",
    }

    class _RepairBackend(_Backend):
        user = ""

        def complete_json(self, **kwargs: object) -> AgentCompletion:
            self.user = str(kwargs["user"])
            return completion

    backend = _RepairBackend()
    repaired, record = propose_test_only_repair(
        prior,
        request=REQUEST,
        public_evaluator_contract="def test_public_contract():\n    assert True\n",
        failure_feedback="One generated black-box assertion failed.",
        backend=backend,
    )

    assert repaired.files[0] == prior.files[0]
    assert repaired.run_command == prior.run_command
    assert repaired.component_decisions == prior.component_decisions
    assert repaired.tests == prior.tests
    assert repaired.files[1].content.startswith("def test_detect_duplicates")
    assert record.locked_file_sha256 == {
        "dupfinder.py": sha256(prior.files[0].content.encode()).hexdigest()
    }
    assert record.ignored_provider_fields == ["provider_note"]
    assert record.prior_test_suite_sha256 != record.repaired_test_suite_sha256
    assert "immutable product" in backend.user.casefold()
    assert "authoritative evaluator" in backend.user


def test_test_only_repair_rejects_product_files_and_missing_test_functions() -> None:
    assert is_generated_test_path("tests/test_program.py") is True
    assert is_generated_test_path("program.py") is False
    prior, _ = propose_gap_system(
        "Build a deterministic duplicate finder that never modifies input files.",
        request=REQUEST,
        discovery=DISCOVERY,
        backend=_Backend(),
    )
    completion = _Backend().complete_json()
    completion.content = {
        "schema_version": "1",
        "files": [{"path": "program.py", "content": "print('rewrite')\n"}],
        "acceptance_coverage": [
            {
                "acceptance_id": "detect-duplicates",
                "test_file": "program.py",
                "test_name": "test_missing",
                "rationale": "This must be rejected before any product rewrite is accepted.",
            }
        ],
        "limitations": [],
    }

    class _InvalidRepairBackend(_Backend):
        def complete_json(self, **_: object) -> AgentCompletion:
            return completion

    with pytest.raises(RepairProposalRejected, match="schema validation") as caught:
        propose_test_only_repair(
            prior,
            request=REQUEST,
            public_evaluator_contract="def test_public_contract():\n    assert True\n",
            failure_feedback="Generated tests failed.",
            backend=_InvalidRepairBackend(),
        )
    serialized = json.loads(caught.value.record.model_dump_json())
    assert serialized["repair_status"] == "schema-rejected"
    assert serialized["validation_errors"][0]["type"] == "value_error"
    assert serialized["locked_file_sha256"]


def test_test_only_repair_rejects_byte_identical_and_previously_rejected_suites() -> None:
    prior, _ = propose_gap_system(
        "Build a deterministic duplicate finder that never modifies input files.",
        request=REQUEST,
        discovery=DISCOVERY,
        backend=_Backend(),
    )
    prior_test_hash = _test_suite_sha256(prior.files)
    completion = _Backend().complete_json()
    completion.content = {
        "schema_version": "1",
        "files": [item.model_dump(mode="json") for item in prior.files if "tests" in item.path],
        "acceptance_coverage": [item.model_dump(mode="json") for item in prior.acceptance_coverage],
        "limitations": [],
    }

    class _RepeatedRepairBackend(_Backend):
        user = ""

        def complete_json(self, **kwargs: object) -> AgentCompletion:
            self.user = str(kwargs["user"])
            return completion

    backend = _RepeatedRepairBackend()
    with pytest.raises(RepairProposalRejected) as caught:
        propose_test_only_repair(
            prior,
            request=REQUEST,
            public_evaluator_contract="def test_public_contract():\n    assert True\n",
            failure_feedback="Generated tests failed.",
            backend=backend,
            rejected_test_suite_sha256s=["a" * 64],
        )

    record = json.loads(caught.value.record.model_dump_json())
    assert record["candidate_test_suite_sha256"] == prior_test_hash
    assert record["prior_test_suite_sha256"] == prior_test_hash
    assert record["rejected_test_suite_sha256s"] == ["a" * 64]
    assert record["validation_errors"][0]["type"] == "value_error.test-repair-repeat"
    assert prior_test_hash in backend.user
    assert "a" * 64 in backend.user


def test_test_suite_hash_is_stable_across_order_and_path_case() -> None:
    files = [
        GeneratedFile(path="tests/test_b.py", content="def test_b():\n    assert True\n"),
        GeneratedFile(path="Tests/test_a.py", content="def test_a():\n    assert True\n"),
    ]

    assert _test_suite_sha256(files) == _test_suite_sha256(list(reversed(files)))


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
