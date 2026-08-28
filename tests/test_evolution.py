from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from blackridge.evolution import (
    ChallengerProposalRejected,
    EvolutionRoundEvaluation,
    propose_challenger_architecture,
    repair_challenger_interfaces,
    select_champion,
)
from blackridge.operator import AgentCompletion, AgentUsage

SHA = "a" * 64
EVIDENCE = "b" * 64


def _candidate(
    candidate_id: str,
    origin: str,
    architecture_line: str,
    effectiveness: float,
    cost: float,
    *,
    safety: bool = True,
    interventions: int = 0,
) -> dict[str, object]:
    return {
        "candidate_id": candidate_id,
        "architecture_line": architecture_line,
        "origin": origin,
        "parent_ids": ["champion-v1-1"],
        "artifact_sha256": EVIDENCE,
        "builder_id": f"builder:{candidate_id}",
        "benchmark_revision_sha256": SHA,
        "gates": [
            {
                "gate_id": "no-data-loss",
                "passed": safety,
                "evidence_sha256": EVIDENCE,
                "observation": "The destructive input-integrity control passed.",
            }
        ],
        "metrics": [
            {
                "metric_id": "effectiveness",
                "value": effectiveness,
                "evidence_sha256": EVIDENCE,
            },
            {"metric_id": "cost", "value": cost, "evidence_sha256": EVIDENCE},
        ],
        "manual_interventions": interventions,
    }


def _round() -> dict[str, object]:
    return {
        "round_id": "round-001",
        "evaluator_id": "independent:evaluator",
        "benchmark": {
            "id": "foundry-evolution-v1",
            "revision_sha256": SHA,
            "public_spec_sha256": EVIDENCE,
            "hidden_holdout_sha256": "c" * 64,
            "gates": [
                {
                    "id": "no-data-loss",
                    "description": "No benchmark task may modify or destroy protected input data.",
                }
            ],
            "metrics": [
                {
                    "id": "effectiveness",
                    "description": (
                        "Fraction of independently evaluated workload outcomes satisfied."
                    ),
                    "direction": "maximize",
                    "weight": 0.8,
                    "lower_bound": 0,
                    "upper_bound": 100,
                },
                {
                    "id": "cost",
                    "description": "Total normalized execution and operator cost for the round.",
                    "direction": "minimize",
                    "weight": 0.2,
                    "lower_bound": 0,
                    "upper_bound": 10,
                },
            ],
        },
        "incumbent_candidate_id": "champion-v1-1",
        "candidates": [
            _candidate("champion-v1-1", "champion", "v1.1", 70, 3),
            _candidate("candidate-a", "A", "v1.2", 75, 3),
            _candidate("candidate-b", "B", "v2", 99, 1, safety=False),
            _candidate("candidate-a-plus-b", "A+B", "v1.2", 90, 4),
            _candidate("candidate-b-plus-a", "B+A", "v2.1", 85, 3),
        ],
    }


def test_critical_gate_rejects_fast_candidate_and_hybrid_wins() -> None:
    selection = select_champion(EvolutionRoundEvaluation.model_validate(_round()))
    assert selection.selected_candidate_id == "candidate-a-plus-b"
    assert selection.selected_architecture_line == "v1.2"
    assert selection.incumbent_retained is False
    rejected = next(item for item in selection.standings if item.candidate_id == "candidate-b")
    assert rejected.eligible is False
    assert rejected.weighted_score is None
    assert rejected.failed_gates == ["no-data-loss"]


def test_exact_tie_retains_incumbent_to_avoid_architecture_churn() -> None:
    value = _round()
    candidates = value["candidates"]
    assert isinstance(candidates, list)
    for candidate in candidates[1:]:
        assert isinstance(candidate, dict)
        candidate["metrics"] = [
            {"metric_id": "effectiveness", "value": 70, "evidence_sha256": EVIDENCE},
            {"metric_id": "cost", "value": 3, "evidence_sha256": EVIDENCE},
        ]
        candidate["manual_interventions"] = 0
        candidate["gates"] = [
            {
                "gate_id": "no-data-loss",
                "passed": True,
                "evidence_sha256": EVIDENCE,
                "observation": "The destructive input-integrity control passed.",
            }
        ]
    selection = select_champion(EvolutionRoundEvaluation.model_validate(value))
    assert selection.selected_candidate_id == "champion-v1-1"
    assert selection.incumbent_retained is True


def test_builder_cannot_be_its_own_evaluator() -> None:
    value = _round()
    value["evaluator_id"] = "builder:candidate-a"
    with pytest.raises(ValidationError, match="evaluator must be independent"):
        EvolutionRoundEvaluation.model_validate(value)


class _ArchitectureBackend:
    identity = "fixture:architecture-builder"

    def complete_json(self, **_: object) -> AgentCompletion:
        components = []
        for component_id, zone, accepts, produces in (
            (
                "policy-ledger",
                "control-plane",
                ["request/v1"],
                ["authorized-build/v1"],
            ),
            (
                "builder-cell",
                "untrusted-builder",
                ["authorized-build/v1"],
                ["candidate-bundle/v1"],
            ),
            (
                "execution-cell",
                "sandbox",
                ["candidate-bundle/v1"],
                ["evaluation-evidence/v1"],
            ),
            (
                "verdict-engine",
                "evaluator",
                ["evaluation-evidence/v1"],
                ["verdict/v1"],
            ),
        ):
            components.append(
                {
                    "id": component_id,
                    "responsibility": (
                        f"Own the isolated {zone} responsibility through a stable boundary."
                    ),
                    "boundary": "api",
                    "trust_zone": zone,
                    "source_policy": "reuse-first",
                    "accepts": accepts,
                    "produces": produces,
                }
            )
        content = {
            "schema_version": "1",
            "architecture_line": "v2",
            "thesis": (
                "Use an append-only event ledger and isolated capability cells so builders never "
                "own orchestration state or evaluation authority, while retaining reproducibility."
            ),
            "design_principles": [
                "append-only state",
                "capability cells",
                "independent verdict authority",
            ],
            "components": components,
            "flows": [
                {
                    "source_component": "policy-ledger",
                    "target_component": "builder-cell",
                    "contract": "authorized-build/v1",
                },
                {
                    "source_component": "builder-cell",
                    "target_component": "execution-cell",
                    "contract": "candidate-bundle/v1",
                },
                {
                    "source_component": "execution-cell",
                    "target_component": "verdict-engine",
                    "contract": "evaluation-evidence/v1",
                },
            ],
            "public_champion_strengths_to_preserve": [
                "fail-closed gates",
                "exact artifact hashes",
                "independent evaluation",
            ],
            "intentional_differences": [
                "event-sourced orchestration",
                "isolated capability cells",
            ],
            "first_vertical_slice": [
                "record one request",
                "build one isolated candidate",
                "evaluate one immutable artifact",
            ],
            "evaluator_experiments": [
                "tamper ledger ordering",
                "deny builder evaluator access",
                "replay one frozen event stream",
            ],
            "risks": [
                "event schema migration",
                "cell startup overhead",
                "distributed trace complexity",
            ],
            "non_goals": ["complete arbitrary production-system autonomy"],
        }
        return AgentCompletion(
            provider="fixture",
            model="architecture",
            finish_reason="stop",
            content=content,
            content_sha256=SHA,
            usage=AgentUsage(),
        )


def test_fresh_challenger_proposal_records_that_champion_source_was_not_provided() -> None:
    proposal, record = propose_challenger_architecture(
        "A public product brief that describes an evidence-driven reuse-first foundry " * 3,
        "A public benchmark with critical gates, fixed metrics, and independent evaluation " * 3,
        backend=_ArchitectureBackend(),
    )
    assert proposal.architecture_line == "v2"
    assert record.champion_source_provided is False
    assert record.proposal_sha256


def test_challenger_retry_binds_validator_feedback_without_champion_source() -> None:
    feedback = "Attempt 001 used unsupported boundary sandbox; use api for that component."
    proposal, record = propose_challenger_architecture(
        "A public product brief that describes an evidence-driven reuse-first foundry " * 3,
        "A public benchmark with critical gates, fixed metrics, and independent evaluation " * 3,
        backend=_ArchitectureBackend(),
        review_feedback=feedback,
    )
    assert proposal.architecture_line == "v2"
    assert record.review_feedback_sha256 is not None
    assert record.champion_source_provided is False


def test_invalid_challenger_completion_is_retained() -> None:
    class _InvalidArchitectureBackend(_ArchitectureBackend):
        def complete_json(self, **kwargs: object) -> AgentCompletion:
            completion = super().complete_json(**kwargs)
            completion.content["components"] = completion.content["components"][:3]
            return completion

    with pytest.raises(ChallengerProposalRejected) as caught:
        propose_challenger_architecture(
            "A public product brief that describes an evidence-driven reuse-first foundry " * 3,
            "A public benchmark with critical gates, fixed metrics, and independent evaluation "
            * 3,
            backend=_InvalidArchitectureBackend(),
        )
    assert caught.value.record.status == "schema-rejected"
    assert caught.value.record.champion_source_provided is False


def test_challenger_flow_contract_must_match_both_component_interfaces() -> None:
    class _MismatchedFlowBackend(_ArchitectureBackend):
        def complete_json(self, **kwargs: object) -> AgentCompletion:
            completion = super().complete_json(**kwargs)
            flows = completion.content["flows"]
            assert isinstance(flows, list)
            first = flows[0]
            assert isinstance(first, dict)
            first["contract"] = "undeclared-contract/v1"
            return completion

    with pytest.raises(ChallengerProposalRejected, match="schema validation") as caught:
        propose_challenger_architecture(
            "A public product brief that describes an evidence-driven reuse-first foundry " * 3,
            "A public benchmark with critical gates, fixed metrics, and independent evaluation "
            * 3,
            backend=_MismatchedFlowBackend(),
        )
    serialized = json.loads(caught.value.record.model_dump_json())
    assert serialized["validation_errors"][0]["type"] == "value_error"

    proposal, repair = repair_challenger_interfaces(
        caught.value.record,
        approved_completion_sha256=SHA,
    )
    assert proposal.architecture_line == "v2"
    assert repair.parent_completion_sha256 == SHA
    assert repair.champion_source_provided is False
    assert [item.model_dump() for item in repair.actions] == [
        {
            "component_id": "policy-ledger",
            "interface": "produces",
            "contract": "undeclared-contract/v1",
        },
        {
            "component_id": "builder-cell",
            "interface": "accepts",
            "contract": "undeclared-contract/v1",
        },
    ]
