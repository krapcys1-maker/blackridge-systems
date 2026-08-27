"""Strict file formats for requests, discovery runs, and blueprints."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from blackridge.adaptation import AdapterExperiment
from blackridge.composition import CompositionDefinition, CompositionPlan
from blackridge.evidence import ManualReview, ProbeEvidence
from blackridge.formats import load_yaml
from blackridge.models import DiscoveryRun, SystemBlueprint, SystemRequest
from blackridge.sandbox import SandboxExperiment
from blackridge.supply_chain import SupplyChainExperiment


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


def write_run(run: DiscoveryRun, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(run.model_dump_json(indent=2), encoding="utf-8", newline="\n")


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
