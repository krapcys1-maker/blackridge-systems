"""Strict file formats for requests, discovery runs, and blueprints."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import yaml
from pydantic import TypeAdapter

from blackridge.adaptation import AdapterExperiment
from blackridge.composition import CompositionDefinition, CompositionPlan
from blackridge.evidence import ManualReview, ProbeEvidence
from blackridge.evolution import (
    ChallengerArchitectureProposal,
    ChallengerInterfaceRepairRecord,
    ChallengerProposalRecord,
    ChallengerRejectionRecord,
    ChampionSelection,
    EvolutionRoundEvaluation,
)
from blackridge.formats import load_yaml
from blackridge.generation import (
    GeneratedSystemProposal,
    GenerationRecord,
    GenerationRejectionRecord,
    VerifiedComponent,
)
from blackridge.models import DiscoveryRun, SystemBlueprint, SystemRequest
from blackridge.planning import PlanningRecord
from blackridge.sandbox import SandboxExperiment
from blackridge.supply_chain import SupplyChainExperiment


def _atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".part",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def load_request(path: Path) -> SystemRequest:
    data = load_yaml(path)
    return SystemRequest.model_validate(data)


def load_sandbox_experiment(path: Path) -> SandboxExperiment:
    data = load_yaml(path)
    return SandboxExperiment.model_validate(data)


def load_adapter_experiment(path: Path) -> AdapterExperiment:
    data = load_yaml(path)
    return AdapterExperiment.model_validate(data)


def load_supply_chain_experiment(path: Path) -> SupplyChainExperiment:
    data = load_yaml(path)
    return SupplyChainExperiment.model_validate(data)


def load_composition_definition(path: Path) -> CompositionDefinition:
    data = load_yaml(path)
    return CompositionDefinition.model_validate(data)


def load_composition_plan(path: Path) -> CompositionPlan:
    data = load_yaml(path)
    return CompositionPlan.model_validate(data)


def load_run(path: Path) -> DiscoveryRun:
    return DiscoveryRun.model_validate_json(path.read_text(encoding="utf-8"))


def load_evolution_round(path: Path) -> EvolutionRoundEvaluation:
    return EvolutionRoundEvaluation.model_validate_json(path.read_text(encoding="utf-8"))


def load_challenger_rejection(path: Path) -> ChallengerRejectionRecord:
    return ChallengerRejectionRecord.model_validate_json(path.read_text(encoding="utf-8"))


def write_run(run: DiscoveryRun, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(run.model_dump_json(indent=2), encoding="utf-8", newline="\n")


def write_request(request: SystemRequest, path: Path) -> None:
    primitive = json.loads(request.model_dump_json())
    _atomic_write_text(path, yaml.safe_dump(primitive, sort_keys=False))


def write_planning_record(record: PlanningRecord, path: Path) -> None:
    _atomic_write_text(path, record.model_dump_json(indent=2))


def load_generated_proposal(path: Path) -> GeneratedSystemProposal:
    return GeneratedSystemProposal.model_validate_json(path.read_text(encoding="utf-8"))


def load_verified_components(path: Path) -> list[VerifiedComponent]:
    return TypeAdapter(list[VerifiedComponent]).validate_json(path.read_text(encoding="utf-8"))


def write_generated_proposal(proposal: GeneratedSystemProposal, path: Path) -> None:
    _atomic_write_text(path, proposal.model_dump_json(indent=2))


def write_generation_record(record: GenerationRecord, path: Path) -> None:
    _atomic_write_text(path, record.model_dump_json(indent=2))


def write_generation_rejection(record: GenerationRejectionRecord, path: Path) -> None:
    _atomic_write_text(path, record.model_dump_json(indent=2))


def write_champion_selection(selection: ChampionSelection, path: Path) -> None:
    _atomic_write_text(path, selection.model_dump_json(indent=2))


def write_challenger_proposal(proposal: ChallengerArchitectureProposal, path: Path) -> None:
    _atomic_write_text(path, proposal.model_dump_json(indent=2))


def write_challenger_proposal_record(record: ChallengerProposalRecord, path: Path) -> None:
    _atomic_write_text(path, record.model_dump_json(indent=2))


def write_challenger_rejection(record: ChallengerRejectionRecord, path: Path) -> None:
    _atomic_write_text(path, record.model_dump_json(indent=2))


def write_challenger_interface_repair(record: ChallengerInterfaceRepairRecord, path: Path) -> None:
    _atomic_write_text(path, record.model_dump_json(indent=2))


def write_blueprint(blueprint: SystemBlueprint, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    primitive = json.loads(blueprint.model_dump_json())
    path.write_text(yaml.safe_dump(primitive, sort_keys=False), encoding="utf-8", newline="\n")


def write_composition_plan(plan: CompositionPlan, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    primitive = json.loads(plan.model_dump_json())
    path.write_text(yaml.safe_dump(primitive, sort_keys=False), encoding="utf-8", newline="\n")


def write_probe(probe: ProbeEvidence, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(probe.model_dump_json(indent=2), encoding="utf-8", newline="\n")


def load_probe(path: Path) -> ProbeEvidence:
    return ProbeEvidence.model_validate_json(path.read_text(encoding="utf-8"))


def write_manual_review(review: ManualReview, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(review.model_dump_json(indent=2), encoding="utf-8", newline="\n")
