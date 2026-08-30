"""Deterministic champion-challenger evaluation with retained lineage."""

from __future__ import annotations

import json
import math
from hashlib import sha256
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from blackridge.operator import AgentBackend, AgentCompletion

CandidateOrigin = Literal["champion", "A", "B", "A+B", "B+A"]
MetricDirection = Literal["maximize", "minimize"]
TrustZone = Literal[
    "control-plane",
    "untrusted-builder",
    "sandbox",
    "evaluator",
    "external-service",
]


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


class ArchitectureComponent(StrictEvolutionModel):
    id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    responsibility: str = Field(min_length=20, max_length=1_000)
    boundary: Literal["cli", "api", "mcp", "oci", "package", "event", "file"]
    trust_zone: TrustZone
    source_policy: Literal["reuse-first", "generate-gap", "hybrid"]
    accepts: list[str] = Field(min_length=1, max_length=20)
    produces: list[str] = Field(min_length=1, max_length=20)


class ArchitectureFlow(StrictEvolutionModel):
    source_component: str
    target_component: str
    contract: str = Field(min_length=5, max_length=240)


class ChallengerArchitectureProposal(StrictEvolutionModel):
    schema_version: Literal["1"] = "1"
    architecture_line: Literal["v2"] = "v2"
    thesis: str = Field(min_length=80, max_length=3_000)
    design_principles: list[str] = Field(min_length=3, max_length=10)
    components: list[ArchitectureComponent] = Field(min_length=4, max_length=20)
    flows: list[ArchitectureFlow] = Field(min_length=3, max_length=40)
    public_champion_strengths_to_preserve: list[str] = Field(min_length=3, max_length=20)
    intentional_differences: list[str] = Field(min_length=2, max_length=20)
    first_vertical_slice: list[str] = Field(min_length=3, max_length=20)
    evaluator_experiments: list[str] = Field(min_length=3, max_length=20)
    risks: list[str] = Field(min_length=3, max_length=20)
    non_goals: list[str] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def coherent_separated_architecture(self) -> ChallengerArchitectureProposal:
        component_ids = [item.id for item in self.components]
        if len(component_ids) != len(set(component_ids)):
            raise ValueError("challenger component ids must be unique")
        known = set(component_ids)
        by_id = {item.id: item for item in self.components}
        for flow in self.flows:
            if flow.source_component not in known or flow.target_component not in known:
                raise ValueError("challenger flows must reference declared components")
            if flow.source_component == flow.target_component:
                raise ValueError("challenger flows cannot be self-referential")
            if flow.contract not in by_id[flow.source_component].produces:
                raise ValueError("every flow contract must be declared by its source component")
            if flow.contract not in by_id[flow.target_component].accepts:
                raise ValueError("every flow contract must be accepted by its target component")
        zones = {item.trust_zone for item in self.components}
        required = {"control-plane", "untrusted-builder", "sandbox", "evaluator"}
        if not required.issubset(zones):
            raise ValueError(
                "challenger must separate control-plane, builder, sandbox, and evaluator"
            )
        return self


class ChallengerProposalRecord(StrictEvolutionModel):
    schema_version: Literal["1"] = "1"
    operator: str
    public_brief_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    public_benchmark_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_feedback_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    champion_source_provided: Literal[False] = False
    completion: AgentCompletion
    proposal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["manual-review-required"] = "manual-review-required"


class ChallengerRejectionRecord(StrictEvolutionModel):
    schema_version: Literal["1"] = "1"
    operator: str
    public_brief_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    public_benchmark_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_feedback_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    champion_source_provided: Literal[False] = False
    completion: AgentCompletion
    validation_errors: list[dict[str, object]] = Field(min_length=1)
    status: Literal["schema-rejected"] = "schema-rejected"


class ChallengerProposalRejected(ValueError):
    def __init__(self, record: ChallengerRejectionRecord) -> None:
        super().__init__("challenger architecture failed deterministic schema validation")
        self.record = record


class ChallengerInterfaceRepairAction(StrictEvolutionModel):
    component_id: str
    interface: Literal["accepts", "produces"]
    contract: str


class ChallengerInterfaceRepairRecord(StrictEvolutionModel):
    schema_version: Literal["1"] = "1"
    repair_operator: Literal["deterministic-interface-repair:v1"] = (
        "deterministic-interface-repair:v1"
    )
    parent_operator: str
    parent_completion_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    champion_source_provided: Literal[False] = False
    actions: list[ChallengerInterfaceRepairAction] = Field(min_length=1)
    proposal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["manual-review-required"] = "manual-review-required"


def _serializable_validation_errors(exc: ValidationError) -> list[dict[str, object]]:
    """Remove non-JSON exception objects from Pydantic validator context."""

    raw = exc.errors(include_url=False, include_input=False)
    return cast(list[dict[str, object]], json.loads(json.dumps(raw, default=str)))


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


def challenger_proposal_sha256(proposal: ChallengerArchitectureProposal) -> str:
    return sha256(proposal.model_dump_json().encode("utf-8")).hexdigest()


def repair_challenger_interfaces(
    rejection: ChallengerRejectionRecord,
    *,
    approved_completion_sha256: str,
) -> tuple[ChallengerArchitectureProposal, ChallengerInterfaceRepairRecord]:
    """Repair only undeclared flow endpoints in an exact rejected completion."""

    if rejection.completion.content_sha256 != approved_completion_sha256:
        raise ValueError("approved completion SHA-256 does not match the rejection record")
    content = cast(
        dict[str, object],
        json.loads(json.dumps(rejection.completion.content)),
    )
    raw_components = content.get("components")
    raw_flows = content.get("flows")
    if not isinstance(raw_components, list) or not isinstance(raw_flows, list):
        raise ValueError("rejected completion has no repairable component graph")
    components = [ArchitectureComponent.model_validate(item) for item in raw_components]
    flows = [ArchitectureFlow.model_validate(item) for item in raw_flows]
    by_id = {item.id: item for item in components}
    actions: list[ChallengerInterfaceRepairAction] = []
    for flow in flows:
        source = by_id.get(flow.source_component)
        target = by_id.get(flow.target_component)
        if source is None or target is None:
            raise ValueError("interface repair cannot invent missing components")
        if flow.contract not in source.produces:
            source.produces.append(flow.contract)
            actions.append(
                ChallengerInterfaceRepairAction(
                    component_id=source.id,
                    interface="produces",
                    contract=flow.contract,
                )
            )
        if flow.contract not in target.accepts:
            target.accepts.append(flow.contract)
            actions.append(
                ChallengerInterfaceRepairAction(
                    component_id=target.id,
                    interface="accepts",
                    contract=flow.contract,
                )
            )
    if not actions:
        raise ValueError("rejected completion has no interface mismatch to repair")
    content["components"] = [item.model_dump(mode="json") for item in components]
    proposal = ChallengerArchitectureProposal.model_validate(content)
    record = ChallengerInterfaceRepairRecord(
        parent_operator=rejection.operator,
        parent_completion_sha256=rejection.completion.content_sha256,
        actions=actions,
        proposal_sha256=challenger_proposal_sha256(proposal),
    )
    return proposal, record


def propose_challenger_architecture(
    public_brief: str,
    public_benchmark: str,
    *,
    backend: AgentBackend,
    review_feedback: str | None = None,
) -> tuple[ChallengerArchitectureProposal, ChallengerProposalRecord]:
    """Propose fresh architecture B without providing champion source code."""

    brief = public_brief.strip()
    benchmark = public_benchmark.strip()
    if len(brief) < 100 or len(benchmark) < 100:
        raise ValueError("public brief and benchmark must each contain at least 100 characters")
    if len(brief.encode("utf-8")) > 200_000:
        raise ValueError("public brief exceeds the 200-kilobyte limit")
    if len(benchmark.encode("utf-8")) > 200_000:
        raise ValueError("public benchmark exceeds the 200-kilobyte limit")
    feedback = review_feedback.strip() if review_feedback is not None else ""
    if len(feedback.encode("utf-8")) > 50_000:
        raise ValueError("challenger review feedback exceeds the 50-kilobyte limit")
    example = {
        "schema_version": "1",
        "architecture_line": "v2",
        "thesis": "A genuinely different architecture thesis of at least eighty characters.",
        "design_principles": ["principle one", "principle two", "principle three"],
        "components": [
            {
                "id": "example-control",
                "responsibility": (
                    "Own deterministic state and enforce all frozen policy decisions."
                ),
                "boundary": "api",
                "trust_zone": "control-plane",
                "source_policy": "reuse-first",
                "accepts": ["request/v1"],
                "produces": ["decision/v1"],
            }
        ],
        "flows": [
            {
                "source_component": "example-control",
                "target_component": "another-declared-component",
                "contract": "decision/v1",
            }
        ],
        "public_champion_strengths_to_preserve": ["strength one", "strength two", "strength three"],
        "intentional_differences": ["difference one", "difference two"],
        "first_vertical_slice": ["step one", "step two", "step three"],
        "evaluator_experiments": ["experiment one", "experiment two", "experiment three"],
        "risks": ["risk one", "risk two", "risk three"],
        "non_goals": ["not a complete autonomous production foundry"],
    }
    system = (
        "You are the isolated builder of architecture B in a champion-challenger round. "
        "You receive no champion source, repository files, prompts, tests, or hidden holdout. "
        "Design a genuinely different v2 architecture from the public contract only. Preserve "
        "publicly stated safety strengths, but do not imitate an unseen implementation. The "
        "builder cannot modify the benchmark or select itself as champion. Return one JSON "
        "object only; architecture claims are proposals, never evidence."
    )
    user = (
        "Return JSON matching this shape and exact keys:\n"
        f"{json.dumps(example, indent=2)}\n\n"
        "Declare 4-20 components. Include separate control-plane, untrusted-builder, sandbox, "
        "and evaluator trust zones. Every flow must reference declared component ids. Prefer "
        "stable reuse boundaries and name what is intentionally different from the champion's "
        "public behavior. Component boundary must be exactly one of: cli, api, mcp, oci, "
        "package, event, file. Every flow contract must appear in the source component's "
        "produces list and the target component's accepts list. Every named architectural "
        "mechanism must map to a declared component or an explicit component responsibility.\n\n"
        f"PUBLIC PRODUCT BRIEF:\n{brief}\n\nPUBLIC BENCHMARK:\n{benchmark}"
    )
    if feedback:
        user += (
            "\n\nVALIDATOR FEEDBACK FROM THE PREVIOUS RETAINED ATTEMPT:\n"
            f"{feedback}\nCorrect only the reported contract violations while preserving a "
            "genuinely independent architecture."
        )
    brief_hash = sha256(brief.encode("utf-8")).hexdigest()
    benchmark_hash = sha256(benchmark.encode("utf-8")).hexdigest()
    feedback_hash = sha256(feedback.encode("utf-8")).hexdigest() if feedback else None
    completion = backend.complete_json(system=system, user=user, max_tokens=16_384)
    try:
        proposal = ChallengerArchitectureProposal.model_validate(completion.content)
    except ValidationError as exc:
        raise ChallengerProposalRejected(
            ChallengerRejectionRecord(
                operator=backend.identity,
                public_brief_sha256=brief_hash,
                public_benchmark_sha256=benchmark_hash,
                review_feedback_sha256=feedback_hash,
                completion=completion,
                validation_errors=_serializable_validation_errors(exc),
            )
        ) from exc
    record = ChallengerProposalRecord(
        operator=backend.identity,
        public_brief_sha256=brief_hash,
        public_benchmark_sha256=benchmark_hash,
        review_feedback_sha256=feedback_hash,
        completion=completion,
        proposal_sha256=challenger_proposal_sha256(proposal),
    )
    return proposal, record
