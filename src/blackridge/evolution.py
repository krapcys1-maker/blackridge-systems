"""Deterministic champion-challenger evaluation with retained lineage."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CandidateOrigin = Literal["champion", "A", "B", "A+B", "B+A"]
MetricDirection = Literal["maximize", "minimize"]


class StrictEvolutionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CriticalGateDefinition(StrictEvolutionModel):
    id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    description: str = Field(min_length=20, max_length=1_000)


class MetricDefinition(StrictEvolutionModel):
    id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    description: str = Field(min_length=20, max_length=1_000)
    direction: MetricDirection
    weight: float = Field(gt=0, le=1)
    lower_bound: float
    upper_bound: float

    @model_validator(mode="after")
    def finite_ordered_bounds(self) -> MetricDefinition:
        values = (self.weight, self.lower_bound, self.upper_bound)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("metric weights and bounds must be finite")
        if self.lower_bound >= self.upper_bound:
            raise ValueError("metric lower_bound must be smaller than upper_bound")
        return self


class EvolutionBenchmark(StrictEvolutionModel):
    schema_version: Literal["1"] = "1"
    id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    revision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    public_spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    hidden_holdout_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    gates: list[CriticalGateDefinition] = Field(min_length=1)
    metrics: list[MetricDefinition] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_ids_and_normalized_weights(self) -> EvolutionBenchmark:
        gate_ids = [item.id for item in self.gates]
        metric_ids = [item.id for item in self.metrics]
        if len(gate_ids) != len(set(gate_ids)):
            raise ValueError("critical gate ids must be unique")
        if len(metric_ids) != len(set(metric_ids)):
            raise ValueError("metric ids must be unique")
        if not math.isclose(sum(item.weight for item in self.metrics), 1.0, abs_tol=1e-9):
            raise ValueError("metric weights must sum to exactly 1")
        return self


class GateObservation(StrictEvolutionModel):
    gate_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    passed: bool
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observation: str = Field(min_length=10, max_length=2_000)


class MetricObservation(StrictEvolutionModel):
    metric_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    value: float
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def finite_value(self) -> MetricObservation:
        if not math.isfinite(self.value):
            raise ValueError("metric observations must be finite")
        return self


class CandidateEvaluation(StrictEvolutionModel):
    candidate_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    architecture_line: str = Field(pattern=r"^v[0-9]+(?:\.[0-9]+)*$")
    origin: CandidateOrigin
    parent_ids: list[str] = Field(min_length=1, max_length=2)
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    builder_id: str = Field(min_length=3, max_length=240)
    benchmark_revision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    gates: list[GateObservation] = Field(min_length=1)
    metrics: list[MetricObservation] = Field(min_length=1)
    manual_interventions: int = Field(ge=0)

    @model_validator(mode="after")
    def unique_observations(self) -> CandidateEvaluation:
        gate_ids = [item.gate_id for item in self.gates]
        metric_ids = [item.metric_id for item in self.metrics]
        if len(gate_ids) != len(set(gate_ids)):
            raise ValueError("candidate gate observations must be unique")
        if len(metric_ids) != len(set(metric_ids)):
            raise ValueError("candidate metric observations must be unique")
        return self


class EvolutionRoundEvaluation(StrictEvolutionModel):
    schema_version: Literal["1"] = "1"
    round_id: str = Field(pattern=r"^round-[0-9]{3,}$")
    evaluator_id: str = Field(min_length=3, max_length=240)
    benchmark: EvolutionBenchmark
    incumbent_candidate_id: str
    candidates: list[CandidateEvaluation] = Field(min_length=5, max_length=5)

    @model_validator(mode="after")
    def complete_and_independent_round(self) -> EvolutionRoundEvaluation:
        candidate_ids = [item.candidate_id for item in self.candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("candidate ids must be unique within a round")
        by_id = {item.candidate_id: item for item in self.candidates}
        incumbent = by_id.get(self.incumbent_candidate_id)
        if incumbent is None or incumbent.origin != "champion":
            raise ValueError("incumbent_candidate_id must identify the champion snapshot")
        origins = {item.origin for item in self.candidates}
        if origins != {"champion", "A", "B", "A+B", "B+A"}:
            raise ValueError("a round requires champion, A, B, A+B, and B+A exactly once")
        if any(item.builder_id == self.evaluator_id for item in self.candidates):
            raise ValueError("the evaluator must be independent from every candidate builder")
        expected_gates = {item.id for item in self.benchmark.gates}
        expected_metrics = {item.id for item in self.benchmark.metrics}
        for candidate in self.candidates:
            if candidate.benchmark_revision_sha256 != self.benchmark.revision_sha256:
                raise ValueError("all candidates must use the exact benchmark revision")
            if {item.gate_id for item in candidate.gates} != expected_gates:
                raise ValueError("candidate gate coverage does not match the benchmark")
            if {item.metric_id for item in candidate.metrics} != expected_metrics:
                raise ValueError("candidate metric coverage does not match the benchmark")
        return self


class CandidateStanding(StrictEvolutionModel):
    candidate_id: str
    architecture_line: str
    origin: CandidateOrigin
    eligible: bool
    failed_gates: list[str]
    weighted_score: float | None
    manual_interventions: int


class ChampionSelection(StrictEvolutionModel):
    schema_version: Literal["1"] = "1"
    round_id: str
    benchmark_revision_sha256: str
    evaluator_id: str
    incumbent_candidate_id: str
    selected_candidate_id: str
    selected_architecture_line: str
    incumbent_retained: bool
    reason: str
    standings: list[CandidateStanding]


def _normalized_metric(value: float, definition: MetricDefinition) -> float:
    if not definition.lower_bound <= value <= definition.upper_bound:
        raise ValueError(f"metric {definition.id!r} is outside its frozen benchmark bounds")
    position = (value - definition.lower_bound) / (definition.upper_bound - definition.lower_bound)
    return position if definition.direction == "maximize" else 1 - position


def select_champion(round_evaluation: EvolutionRoundEvaluation) -> ChampionSelection:
    """Apply critical gates first, then frozen multidimensional scoring."""

    metrics = {item.id: item for item in round_evaluation.benchmark.metrics}
    standings: list[CandidateStanding] = []
    for candidate in round_evaluation.candidates:
        failed_gates = sorted(item.gate_id for item in candidate.gates if not item.passed)
        weighted_score: float | None = None
        if not failed_gates:
            weighted_score = round(
                sum(
                    _normalized_metric(observation.value, metrics[observation.metric_id])
                    * metrics[observation.metric_id].weight
                    for observation in candidate.metrics
                )
                * 100,
                8,
            )
        standings.append(
            CandidateStanding(
                candidate_id=candidate.candidate_id,
                architecture_line=candidate.architecture_line,
                origin=candidate.origin,
                eligible=not failed_gates,
                failed_gates=failed_gates,
                weighted_score=weighted_score,
                manual_interventions=candidate.manual_interventions,
            )
        )

    eligible = [item for item in standings if item.eligible]
    if not eligible:
        raise ValueError("no candidate passed every critical gate")
    best_score = max(item.weighted_score or 0 for item in eligible)
    finalists = [
        item
        for item in eligible
        if math.isclose(item.weighted_score or 0, best_score, abs_tol=1e-9)
    ]
    fewest_interventions = min(item.manual_interventions for item in finalists)
    finalists = [item for item in finalists if item.manual_interventions == fewest_interventions]
    incumbent = next(
        item for item in standings if item.candidate_id == round_evaluation.incumbent_candidate_id
    )
    selected = (
        incumbent
        if incumbent in finalists
        else sorted(finalists, key=lambda item: item.candidate_id)[0]
    )
    retained = selected.candidate_id == incumbent.candidate_id
    reason = (
        "The incumbent remains champion after critical gates, frozen weighted metrics, and "
        "the manual-intervention tie-break."
        if retained
        else "A challenger passed every critical gate and achieved the strongest frozen "
        "multidimensional result after the manual-intervention tie-break."
    )
    return ChampionSelection(
        round_id=round_evaluation.round_id,
        benchmark_revision_sha256=round_evaluation.benchmark.revision_sha256,
        evaluator_id=round_evaluation.evaluator_id,
        incumbent_candidate_id=round_evaluation.incumbent_candidate_id,
        selected_candidate_id=selected.candidate_id,
        selected_architecture_line=selected.architecture_line,
        incumbent_retained=retained,
        reason=reason,
        standings=sorted(standings, key=lambda item: item.candidate_id),
    )
