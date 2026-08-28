from __future__ import annotations

import pytest
from pydantic import ValidationError

from blackridge.evolution import EvolutionRoundEvaluation, select_champion

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
