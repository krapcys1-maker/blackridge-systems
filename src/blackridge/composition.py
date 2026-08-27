"""Compatibility solving, provenance-locked generation, and graph system execution."""

from __future__ import annotations

import asyncio
import heapq
import json
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Callable, Sequence
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
from itertools import product
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import jsonpatch
import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import BaseModel, ConfigDict, Field, model_validator

from blackridge.composition_evidence import EvidenceReference
from blackridge.composition_evidence import verify_evidence as _verify_evidence
from blackridge.errors import BlackridgeError
from blackridge.evidence import ProbeEvidence
from blackridge.formats import load_yaml
from blackridge.models import EvidenceLevel
from blackridge.process_boundary import run_bounded

COMPOSITION_SOURCE = (
    "https://github.com/krapcys1-maker/blackridge-systems/tree/main/src/blackridge/composition.py"
)
JSON_PATCH_SOURCE = "https://github.com/stefankoegl/python-json-patch/tree/v1.33"
JSON_SCHEMA_SOURCE = "https://github.com/python-jsonschema/jsonschema/tree/v4.26.0"


class StrictModel(BaseModel):
    """Reject undeclared fields in composition control files."""

    model_config = ConfigDict(extra="forbid")


class ContractDefinition(StrictModel):
    contract_id: str = Field(
        min_length=3,
        pattern=r"^[a-z0-9]+(?:[./-][a-z0-9]+)*$",
    )
    schema_definition: dict[str, object] = Field(alias="schema")


class ComponentResource(StrictModel):
    resource_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    source_uri: str = Field(min_length=3)
    revision: str = Field(min_length=7)
    license_spdx: str = Field(min_length=2)
    artifact_file: str = Field(min_length=1)
    artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    copy_timeout_seconds: int = Field(default=300, ge=5, le=1800)


class ComponentLaunch(StrictModel):
    argv: list[str] = Field(min_length=1)
    artifact_file: str = Field(min_length=1)
    artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    working_directory: str = "{definition_dir}"
    timeout_seconds: float = Field(default=30, gt=0, le=300)
    environment_allowlist: list[str] = Field(default_factory=list)
    resources: list[ComponentResource] = Field(default_factory=list)

    @model_validator(mode="after")
    def resource_ids_are_unique(self) -> ComponentLaunch:
        resource_ids = [resource.resource_id for resource in self.resources]
        if len(resource_ids) != len(set(resource_ids)):
            raise ValueError("component launch resource ids must be unique")
        return self


class ComponentOption(StrictModel):
    """One replaceable implementation behind explicit contract boundaries."""

    component_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    capability_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    source_uri: str = Field(min_length=3)
    revision: str = Field(min_length=7)
    license_spdx: str = Field(min_length=2)
    integration: Literal["command-json", "python-library", "cli", "api", "oci"]
    accepts: list[str] = Field(min_length=1, max_length=20)
    produces: list[str] = Field(min_length=1, max_length=1)
    launch: ComponentLaunch | None = None
    evidence: EvidenceReference
    selection_priority: int = Field(default=100, ge=0)
    blocked_reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def command_integration_has_launch(self) -> ComponentOption:
        if len(self.accepts) != len(set(self.accepts)):
            raise ValueError("component input contracts must be unique")
        if self.integration == "command-json" and self.launch is None:
            raise ValueError("command-json components require a launch definition")
        if self.integration != "command-json" and self.launch is not None:
            raise ValueError("only command-json components may declare a launch definition")
        return self


class AdapterOption(StrictModel):
    adapter_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    source_contract: str = Field(min_length=3)
    target_contract: str = Field(min_length=3)
    source_uri: str = Field(min_length=3)
    revision: str = Field(min_length=7)
    license_spdx: str = Field(min_length=2)
    operations: list[dict[str, object]] = Field(min_length=1)
    operations_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence: EvidenceReference
    blocked_reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def operations_are_explicit_json_patch(self) -> AdapterOption:
        allowed = {"add", "remove", "replace", "move", "copy", "test"}
        for index, operation in enumerate(self.operations):
            if operation.get("op") not in allowed:
                raise ValueError(f"adapter operation {index} has an unsupported op")
            path = operation.get("path")
            if not isinstance(path, str) or (path and not path.startswith("/")):
                raise ValueError(f"adapter operation {index} has an invalid path")
        return self


class SandboxResources(StrictModel):
    """Explicit bounded resources carried into the generated runtime lock."""

    memory_mb: int = Field(default=1024, ge=128, le=32768)
    cpus: float = Field(default=2.0, ge=0.25, le=32)
    pids: int = Field(default=256, ge=32, le=4096)


class SandboxImage(StrictModel):
    """Immutable dependency image identity carried by a generated system."""

    reference: str = Field(
        pattern=r"^(?:sha256:[a-f0-9]{64}|[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:[a-f0-9]{64})$"
    )
    expected_id: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")


class CompositionDefinition(StrictModel):
    """A frozen contract-graph composition problem and all permitted choices."""

    schema_version: Literal["1"] = "1"
    name: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    goal: str = Field(min_length=20)
    mode: Literal["calibration", "production"] = "production"
    external_input: str
    required_output: str
    required_capabilities: list[str] = Field(min_length=1)
    allowed_licenses: list[str] = Field(min_length=1)
    allowed_integrations: list[str] = Field(min_length=1)
    minimum_evidence_level: EvidenceLevel = EvidenceLevel.CONTRACT_TESTED
    max_combinations: int = Field(default=10_000, ge=1, le=100_000)
    sandbox_resources: SandboxResources = Field(default_factory=SandboxResources)
    sandbox_image: SandboxImage | None = None
    contracts: list[ContractDefinition] = Field(min_length=2)
    components: list[ComponentOption] = Field(min_length=1)
    adapters: list[AdapterOption] = Field(default_factory=list)

    @model_validator(mode="after")
    def identifiers_and_contracts_are_consistent(self) -> CompositionDefinition:
        def require_unique(values: list[str], label: str) -> None:
            if len(values) != len(set(values)):
                raise ValueError(f"{label} must be unique")

        require_unique(self.required_capabilities, "required capabilities")
        require_unique([item.contract_id for item in self.contracts], "contract ids")
        require_unique([item.component_id for item in self.components], "component ids")
        require_unique([item.adapter_id for item in self.adapters], "adapter ids")
        contract_ids = {item.contract_id for item in self.contracts}
        referenced = {self.external_input, self.required_output}
        for component in self.components:
            referenced.update(component.accepts)
            referenced.update(component.produces)
        for adapter in self.adapters:
            referenced.add(adapter.source_contract)
            referenced.add(adapter.target_contract)
        missing = sorted(referenced - contract_ids)
        if missing:
            raise ValueError("undefined contracts: " + ", ".join(missing))
        component_capabilities = {item.capability_id for item in self.components}
        absent = sorted(set(self.required_capabilities) - component_capabilities)
        if absent:
            raise ValueError("capabilities without component options: " + ", ".join(absent))
        if (
            self.mode == "production"
            and self.minimum_evidence_level < EvidenceLevel.CONTRACT_TESTED
        ):
            raise ValueError("production compositions require at least L3 contract evidence")
        for contract in self.contracts:
            try:
                Draft202012Validator.check_schema(contract.schema_definition)
            except SchemaError as exc:
                raise ValueError(
                    f"invalid schema for {contract.contract_id}: {exc.message}"
                ) from exc
        return self


class QualificationObservation(StrictModel):
    subject_type: Literal["component", "adapter"]
    subject_id: str
    eligible: bool
    reasons: list[str]
    evidence_observations: dict[str, object]


class PlanStep(StrictModel):
    index: int = Field(ge=1)
    step_type: Literal["component", "adapter"]
    subject_id: str
    input_contract: str
    additional_input_contracts: list[str] = Field(default_factory=list)
    output_contract: str

    @model_validator(mode="after")
    def input_contracts_are_unique(self) -> PlanStep:
        inputs = [self.input_contract, *self.additional_input_contracts]
        if len(inputs) != len(set(inputs)):
            raise ValueError("plan step input contracts must be unique")
        return self


class CombinationObservation(StrictModel):
    component_ids: list[str]
    complete: bool
    adapter_count: int
    steps: list[PlanStep]
    unresolved_capabilities: list[str]
    terminal_contract: str
    available_contracts: list[str] = Field(default_factory=list)


class CompositionPlan(StrictModel):
    schema_version: Literal["1"] = "1"
    generated_at: datetime
    definition_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    solver_module_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    system_name: str
    mode: Literal["calibration", "production"]
    complete: bool
    release_ready: bool = False
    selected_component_ids: list[str]
    selected_adapter_ids: list[str]
    steps: list[PlanStep]
    unresolved_capabilities: list[str]
    qualifications: list[QualificationObservation]
    evaluated_combinations: list[CombinationObservation]
    warnings: list[str]


class GeneratedSystem(StrictModel):
    output_directory: str
    definition_sha256: str
    plan_sha256: str
    execution_ready: bool
    release_ready: bool
    artifact_sha256: dict[str, str]


def _sha256_file(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _sha256_json(value: object) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(canonical).hexdigest()


def _is_immutable_revision(value: str) -> bool:
    normalized = value.removeprefix("sha256:")
    if len(normalized) in {40, 64}:
        try:
            int(normalized, 16)
        except ValueError:
            return False
        return True
    if "@sha256:" in value:
        digest = value.rsplit("@sha256:", 1)[1]
        return len(digest) == 64 and all(character in "0123456789abcdef" for character in digest)
    return False


def _qualify_component(
    component: ComponentOption,
    definition: CompositionDefinition,
    definition_directory: Path,
) -> QualificationObservation:
    reasons = list(component.blocked_reasons)
    if component.license_spdx not in definition.allowed_licenses:
        reasons.append(f"license {component.license_spdx} is not allowed")
    if component.integration not in definition.allowed_integrations:
        reasons.append(f"integration {component.integration} is not allowed")
    if component.integration != "command-json":
        reasons.append(f"v1 runtime does not implement {component.integration} components")
    if component.evidence.level < definition.minimum_evidence_level:
        reasons.append(
            f"evidence L{int(component.evidence.level)} is below required "
            f"L{int(definition.minimum_evidence_level)}"
        )
    if definition.mode == "production" and not _is_immutable_revision(component.revision):
        reasons.append("production component revision is not an immutable commit or digest")
    evidence_reasons, observations = _verify_evidence(
        component.evidence,
        definition_directory=definition_directory,
        mode=definition.mode,
        subject_type="component",
        subject_revision=component.revision,
        subject_license_spdx=component.license_spdx,
        artifact_sha256=(
            component.launch.artifact_sha256 if component.launch is not None else None
        ),
    )
    reasons.extend(evidence_reasons)
    if component.launch is not None:
        artifact_value = component.launch.artifact_file.replace(
            "{definition_dir}", str(definition_directory.resolve())
        )
        artifact_path = Path(artifact_value).resolve()
        observations["launch_artifact_file"] = str(artifact_path)
        observations["launch_artifact_exists"] = artifact_path.is_file()
        if not artifact_path.is_file():
            reasons.append("command launch artifact does not exist")
        else:
            actual_hash = _sha256_file(artifact_path)
            expected_hash = component.launch.artifact_sha256
            observations["launch_artifact_sha256"] = actual_hash
            observations["launch_artifact_hash_matches"] = actual_hash == expected_hash
            if actual_hash != expected_hash:
                reasons.append("command launch artifact SHA-256 does not match its lock")
        expanded_working_directory = component.launch.working_directory.replace(
            "{definition_dir}", str(definition_directory.resolve())
        )
        expanded_argv = [
            value.replace("{python}", sys.executable).replace(
                "{definition_dir}", str(definition_directory.resolve())
            )
            for value in component.launch.argv
        ]
        working_directory = Path(expanded_working_directory).resolve()
        argv_paths = {
            (Path(value) if Path(value).is_absolute() else working_directory / value).resolve()
            for value in expanded_argv
        }
        observations["launch_artifact_referenced_by_argv"] = artifact_path in argv_paths
        if artifact_path not in argv_paths:
            reasons.append("locked command artifact is not referenced by argv")
        resource_observations: list[dict[str, object]] = []
        for resource in component.launch.resources:
            resource_value = resource.artifact_file.replace(
                "{definition_dir}", str(definition_directory.resolve())
            )
            resource_path = Path(resource_value).resolve()
            marker = f"{{resource:{resource.resource_id}}}"
            referenced = marker in component.launch.argv or resource_path in argv_paths
            resource_observation: dict[str, object] = {
                "resource_id": resource.resource_id,
                "artifact_file": str(resource_path),
                "exists": resource_path.is_file(),
                "expected_sha256": resource.artifact_sha256,
                "referenced_by_argv": referenced,
                "license_allowed": resource.license_spdx in definition.allowed_licenses,
            }
            if resource.license_spdx not in definition.allowed_licenses:
                reasons.append(
                    f"resource {resource.resource_id} license "
                    f"{resource.license_spdx} is not allowed"
                )
            if definition.mode == "production" and not _is_immutable_revision(resource.revision):
                reasons.append(
                    f"production resource {resource.resource_id} revision is not immutable"
                )
            if not resource_path.is_file():
                reasons.append(f"resource {resource.resource_id} artifact does not exist")
            else:
                actual_resource_hash = _sha256_file(resource_path)
                resource_observation["actual_sha256"] = actual_resource_hash
                resource_observation["hash_matches"] = (
                    actual_resource_hash == resource.artifact_sha256
                )
                if actual_resource_hash != resource.artifact_sha256:
                    reasons.append(
                        f"resource {resource.resource_id} SHA-256 does not match its lock"
                    )
            if not referenced:
                reasons.append(f"resource {resource.resource_id} is not referenced by argv")
            resource_observations.append(resource_observation)
        observations["resources"] = resource_observations
    return QualificationObservation(
        subject_type="component",
        subject_id=component.component_id,
        eligible=not reasons,
        reasons=reasons,
        evidence_observations=observations,
    )


def _qualify_adapter(
    adapter: AdapterOption,
    definition: CompositionDefinition,
    definition_directory: Path,
) -> QualificationObservation:
    reasons = list(adapter.blocked_reasons)
    if adapter.license_spdx not in definition.allowed_licenses:
        reasons.append(f"license {adapter.license_spdx} is not allowed")
    if adapter.evidence.level < definition.minimum_evidence_level:
        reasons.append(
            f"evidence L{int(adapter.evidence.level)} is below required "
            f"L{int(definition.minimum_evidence_level)}"
        )
    if definition.mode == "production" and not _is_immutable_revision(adapter.revision):
        reasons.append("production adapter revision is not an immutable commit or digest")
    evidence_reasons, observations = _verify_evidence(
        adapter.evidence,
        definition_directory=definition_directory,
        mode=definition.mode,
        subject_type="adapter",
        subject_revision=adapter.revision,
        subject_license_spdx=adapter.license_spdx,
        artifact_sha256=adapter.operations_sha256,
    )
    reasons.extend(evidence_reasons)
    operations_hash = _sha256_json(adapter.operations)
    observations["operations_sha256"] = operations_hash
    observations["operations_hash_matches"] = operations_hash == adapter.operations_sha256
    if operations_hash != adapter.operations_sha256:
        reasons.append("adapter operations SHA-256 does not match its lock")
    return QualificationObservation(
        subject_type="adapter",
        subject_id=adapter.adapter_id,
        eligible=not reasons,
        reasons=reasons,
        evidence_observations=observations,
    )


def _route_combination(
    definition: CompositionDefinition,
    components: tuple[ComponentOption, ...],
    adapters: list[AdapterOption],
) -> CombinationObservation:
    selected = {component.component_id: component for component in components}
    required_ids = frozenset(selected)
    queue: list[
        tuple[
            int,
            int,
            tuple[str, ...],
            tuple[str, ...],
            frozenset[str],
            tuple[PlanStep, ...],
        ]
    ] = [(0, 0, (), (definition.external_input,), frozenset(), ())]
    visited: dict[tuple[frozenset[str], frozenset[str]], tuple[int, int, tuple[str, ...]]] = {}
    furthest: tuple[frozenset[str], frozenset[str], tuple[PlanStep, ...]] = (
        frozenset({definition.external_input}),
        frozenset(),
        (),
    )

    while queue:
        adapter_count, step_count, path_key, available_tuple, executed, steps = heapq.heappop(queue)
        available = frozenset(available_tuple)
        state = (available, executed)
        cost = (adapter_count, step_count, path_key)
        if state in visited and visited[state] <= cost:
            continue
        visited[state] = cost
        if len(executed) > len(furthest[1]) or (
            len(executed) == len(furthest[1])
            and (
                len(available) > len(furthest[0])
                or (len(available) == len(furthest[0]) and len(steps) < len(furthest[2]))
            )
        ):
            furthest = (available, executed, steps)
        if executed == required_ids and definition.required_output in available:
            return CombinationObservation(
                component_ids=sorted(selected),
                complete=True,
                adapter_count=adapter_count,
                steps=list(steps),
                unresolved_capabilities=[],
                terminal_contract=definition.required_output,
                available_contracts=sorted(available),
            )

        actions: list[tuple[Literal["component", "adapter"], str, tuple[str, ...], str]] = []
        for component in components:
            if component.component_id not in executed and set(component.accepts) <= available:
                actions.append(
                    (
                        "component",
                        component.component_id,
                        tuple(component.accepts),
                        component.produces[0],
                    )
                )
        for adapter in adapters:
            if adapter.source_contract in available and adapter.target_contract not in available:
                actions.append(
                    (
                        "adapter",
                        adapter.adapter_id,
                        (adapter.source_contract,),
                        adapter.target_contract,
                    )
                )
        actions.sort(key=lambda item: (item[0], item[1]))
        for step_type, subject_id, input_contracts, output_contract in actions:
            next_executed = executed
            next_adapter_count = adapter_count
            if step_type == "component":
                next_executed = executed | {subject_id}
            else:
                next_adapter_count += 1
            next_step = PlanStep(
                index=step_count + 1,
                step_type=step_type,
                subject_id=subject_id,
                input_contract=input_contracts[0],
                additional_input_contracts=list(input_contracts[1:]),
                output_contract=output_contract,
            )
            next_path = (*path_key, f"{step_type}:{subject_id}")
            next_available = tuple(sorted(available | {output_contract}))
            heapq.heappush(
                queue,
                (
                    next_adapter_count,
                    step_count + 1,
                    next_path,
                    next_available,
                    next_executed,
                    (*steps, next_step),
                ),
            )

    unresolved = sorted(
        {selected[component_id].capability_id for component_id in required_ids - furthest[1]}
    )
    terminal_contract = (
        definition.required_output
        if definition.required_output in furthest[0]
        else (furthest[2][-1].output_contract if furthest[2] else definition.external_input)
    )
    return CombinationObservation(
        component_ids=sorted(selected),
        complete=False,
        adapter_count=sum(step.step_type == "adapter" for step in furthest[2]),
        steps=list(furthest[2]),
        unresolved_capabilities=unresolved,
        terminal_contract=terminal_contract,
        available_contracts=sorted(furthest[0]),
    )


def solve_composition(
    definition: CompositionDefinition,
    *,
    definition_file: Path,
    now: datetime | None = None,
) -> CompositionPlan:
    """Select qualified components and the fewest verified contract adapters."""

    definition_directory = definition_file.resolve().parent
    definition_hash = _sha256_file(definition_file)
    component_qualifications = [
        _qualify_component(component, definition, definition_directory)
        for component in definition.components
    ]
    adapter_qualifications = [
        _qualify_adapter(adapter, definition, definition_directory)
        for adapter in definition.adapters
    ]
    qualifications = [*component_qualifications, *adapter_qualifications]
    component_status = {item.subject_id: item for item in component_qualifications}
    adapter_status = {item.subject_id: item for item in adapter_qualifications}
    options_by_capability: list[list[ComponentOption]] = []
    missing_capabilities: list[str] = []
    for capability_id in definition.required_capabilities:
        options = [
            component
            for component in definition.components
            if component.capability_id == capability_id
            and component_status[component.component_id].eligible
        ]
        options.sort(key=lambda item: (item.selection_priority, item.component_id))
        options_by_capability.append(options)
        if not options:
            missing_capabilities.append(capability_id)

    warnings = [
        "A complete solver plan is generation evidence, not an L4 system-verification verdict."
    ]
    if definition.mode == "calibration":
        warnings.append(
            "Calibration mode may use L0 fixtures without manual promotion and is never "
            "release-ready."
        )
    if missing_capabilities:
        warnings.append("No eligible implementation exists for at least one required capability.")
        return CompositionPlan(
            generated_at=(now or datetime.now(UTC)).astimezone(UTC),
            definition_sha256=definition_hash,
            solver_module_sha256=_sha256_file(Path(__file__)),
            system_name=definition.name,
            mode=definition.mode,
            complete=False,
            selected_component_ids=[],
            selected_adapter_ids=[],
            steps=[],
            unresolved_capabilities=missing_capabilities,
            qualifications=qualifications,
            evaluated_combinations=[],
            warnings=warnings,
        )

    combination_count = 1
    for options in options_by_capability:
        combination_count *= len(options)
    if combination_count > definition.max_combinations:
        raise BlackridgeError(
            f"composition search would evaluate {combination_count} combinations; "
            f"limit is {definition.max_combinations}"
        )

    eligible_adapters = [
        adapter for adapter in definition.adapters if adapter_status[adapter.adapter_id].eligible
    ]
    evaluated = [
        _route_combination(definition, combination, eligible_adapters)
        for combination in product(*options_by_capability)
    ]
    complete = [item for item in evaluated if item.complete]
    if not complete:
        best_partial = max(
            evaluated,
            key=lambda item: (
                len(definition.required_capabilities) - len(item.unresolved_capabilities),
                -item.adapter_count,
                tuple(item.component_ids),
            ),
        )
        warnings.append("Eligible components exist, but no complete contract route was found.")
        return CompositionPlan(
            generated_at=(now or datetime.now(UTC)).astimezone(UTC),
            definition_sha256=definition_hash,
            solver_module_sha256=_sha256_file(Path(__file__)),
            system_name=definition.name,
            mode=definition.mode,
            complete=False,
            selected_component_ids=best_partial.component_ids,
            selected_adapter_ids=[
                step.subject_id for step in best_partial.steps if step.step_type == "adapter"
            ],
            steps=best_partial.steps,
            unresolved_capabilities=best_partial.unresolved_capabilities,
            qualifications=qualifications,
            evaluated_combinations=evaluated,
            warnings=warnings,
        )

    component_by_id = {item.component_id: item for item in definition.components}

    def objective(item: CombinationObservation) -> tuple[object, ...]:
        priority = sum(
            component_by_id[component_id].selection_priority for component_id in item.component_ids
        )
        adapter_ids = tuple(step.subject_id for step in item.steps if step.step_type == "adapter")
        return item.adapter_count, priority, tuple(item.component_ids), adapter_ids

    chosen = min(complete, key=objective)
    return CompositionPlan(
        generated_at=(now or datetime.now(UTC)).astimezone(UTC),
        definition_sha256=definition_hash,
        solver_module_sha256=_sha256_file(Path(__file__)),
        system_name=definition.name,
        mode=definition.mode,
        complete=True,
        selected_component_ids=chosen.component_ids,
        selected_adapter_ids=[
            step.subject_id for step in chosen.steps if step.step_type == "adapter"
        ],
        steps=chosen.steps,
        unresolved_capabilities=[],
        qualifications=qualifications,
        evaluated_combinations=evaluated,
        warnings=warnings,
    )


def _write_yaml(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8", newline="\n")


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8", newline="\n")


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value.rstrip() + "\n", encoding="utf-8", newline="\n")


def _primitive(model: BaseModel) -> dict[str, object]:
    return json.loads(model.model_dump_json(by_alias=True))


def _plan_signature(plan: CompositionPlan) -> dict[str, object]:
    return plan.model_dump(mode="json", exclude={"generated_at"})


def _expand_control_value(value: str, definition_directory: Path) -> str:
    return value.replace("{python}", sys.executable).replace(
        "{definition_dir}", str(definition_directory.resolve())
    )


def _portable_component_argv(
    values: Sequence[str],
    *,
    definition_directory: Path,
    source_artifact: Path,
    source_resources: dict[str, Path],
    component_id: str,
) -> list[str]:
    """Replace host-bound launch paths with runtime-resolved bundle markers."""

    portable: list[str] = []
    for value in values:
        if value == "{python}":
            portable.append("{python}")
            continue
        if value.startswith("{resource:") and value.endswith("}"):
            resource_id = value.removeprefix("{resource:").removesuffix("}")
            if resource_id not in source_resources:
                raise BlackridgeError(
                    f"selected component references an unknown resource marker: {component_id}"
                )
            portable.append(value)
            continue
        expanded = value.replace("{definition_dir}", str(definition_directory.resolve()))
        candidate = Path(expanded)
        if candidate.is_absolute() and candidate.resolve() == source_artifact:
            portable.append("{artifact}")
        elif candidate.is_absolute() and candidate.resolve() in source_resources.values():
            resource_id = next(
                item_id
                for item_id, source in source_resources.items()
                if source == candidate.resolve()
            )
            portable.append(f"{{resource:{resource_id}}}")
        elif "{definition_dir}" in value or candidate.is_absolute():
            raise BlackridgeError(
                f"selected component has an unmapped host path in argv: {component_id}"
            )
        elif "{python}" in value or "{artifact}" in value or "{resource:" in value:
            raise BlackridgeError(
                f"selected component uses a launch marker inside another argument: {component_id}"
            )
        else:
            portable.append(value)
    if "{artifact}" not in portable:
        raise BlackridgeError(
            f"selected component argv does not reference its locked artifact: {component_id}"
        )
    for resource_id in source_resources:
        if f"{{resource:{resource_id}}}" not in portable:
            raise BlackridgeError(
                f"selected component argv does not reference resource {resource_id}: {component_id}"
            )
    return portable


def generate_system(
    definition: CompositionDefinition,
    plan: CompositionPlan,
    *,
    definition_file: Path,
    output_directory: Path,
    now: datetime | None = None,
) -> GeneratedSystem:
    """Render a complete, locked system bundle without silently overwriting an existing one."""

    if output_directory.exists():
        raise BlackridgeError(f"generated system target already exists: {output_directory}")
    if not plan.complete:
        raise BlackridgeError("cannot generate a system from an incomplete compatibility plan")
    fresh_plan = solve_composition(
        definition, definition_file=definition_file, now=plan.generated_at
    )
    if _plan_signature(plan) != _plan_signature(fresh_plan):
        raise BlackridgeError("composition plan no longer matches the frozen definition and solver")

    component_by_id = {item.component_id: item for item in definition.components}
    adapter_by_id = {item.adapter_id: item for item in definition.adapters}
    contract_by_id = {item.contract_id: item for item in definition.contracts}
    definition_directory = definition_file.resolve().parent
    output_directory = output_directory.resolve()
    output_directory.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output_directory.name}-", dir=output_directory.parent)
    )
    try:
        (temporary / "composition.definition.yaml").write_bytes(definition_file.read_bytes())
        _write_json(temporary / "composition.plan.json", _primitive(plan))
        contract_files: dict[str, str] = {}
        for contract_id, contract in sorted(contract_by_id.items()):
            filename = contract_id.replace("/", "--") + ".schema.json"
            relative = f"contracts/{filename}"
            _write_json(temporary / relative, contract.schema_definition)
            contract_files[contract_id] = relative

        selected_components = [
            component_by_id[component_id] for component_id in plan.selected_component_ids
        ]
        selected_adapters = [adapter_by_id[adapter_id] for adapter_id in plan.selected_adapter_ids]
        bundled_components: dict[str, tuple[Path, str]] = {}
        bundled_resources: dict[str, dict[str, tuple[Path, str]]] = {}
        for component in selected_components:
            if component.launch is None:
                raise BlackridgeError(
                    "selected component is not executable by the v1 runtime: "
                    f"{component.component_id}"
                )
            source = Path(
                _expand_control_value(component.launch.artifact_file, definition_directory)
            ).resolve()
            suffix = source.suffix if re.fullmatch(r"\.[A-Za-z0-9]+", source.suffix) else ""
            relative = f"components/{component.component_id}{suffix}"
            destination = temporary / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            copied_hash = _sha256_file(destination)
            if copied_hash != component.launch.artifact_sha256:
                raise BlackridgeError(
                    "selected component changed while generating its bundle: "
                    f"{component.component_id}"
                )
            bundled_components[component.component_id] = (source, relative)
            component_resources: dict[str, tuple[Path, str]] = {}
            for resource in component.launch.resources:
                resource_source = Path(
                    _expand_control_value(resource.artifact_file, definition_directory)
                ).resolve()
                resource_suffix = (
                    resource_source.suffix
                    if re.fullmatch(r"\.[A-Za-z0-9]+", resource_source.suffix)
                    else ""
                )
                resource_relative = (
                    f"resources/{component.component_id}/{resource.resource_id}{resource_suffix}"
                )
                resource_destination = temporary / resource_relative
                resource_destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(resource_source, resource_destination)
                if _sha256_file(resource_destination) != resource.artifact_sha256:
                    raise BlackridgeError(
                        "selected component resource changed while generating its bundle: "
                        f"{component.component_id}/{resource.resource_id}"
                    )
                component_resources[resource.resource_id] = (
                    resource_source,
                    resource_relative,
                )
            bundled_resources[component.component_id] = component_resources
        component_locks = [_primitive(component) for component in selected_components]
        _write_yaml(
            temporary / "components.lock.yaml",
            {
                "schema_version": "1",
                "system_name": definition.name,
                "components": component_locks,
            },
        )
        for adapter in selected_adapters:
            _write_yaml(temporary / "adapters" / f"{adapter.adapter_id}.yaml", _primitive(adapter))

        runtime_steps: list[dict[str, object]] = []
        for step in plan.steps:
            runtime_step = _primitive(step)
            if step.step_type == "component":
                component = component_by_id[step.subject_id]
                if component.launch is None:
                    raise BlackridgeError(
                        "selected component is not executable by the v1 runtime: "
                        f"{component.component_id}"
                    )
                source_artifact, bundled_artifact = bundled_components[component.component_id]
                component_resources = bundled_resources[component.component_id]
                runtime_step["launch"] = {
                    "argv": _portable_component_argv(
                        component.launch.argv,
                        definition_directory=definition_directory,
                        source_artifact=source_artifact,
                        source_resources={
                            resource_id: source
                            for resource_id, (source, _relative) in component_resources.items()
                        },
                        component_id=component.component_id,
                    ),
                    "working_directory": "components",
                    "artifact_file": bundled_artifact,
                    "artifact_sha256": component.launch.artifact_sha256,
                    "timeout_seconds": component.launch.timeout_seconds,
                    "environment_allowlist": component.launch.environment_allowlist,
                    "resources": [
                        {
                            "resource_id": resource.resource_id,
                            "source_uri": resource.source_uri,
                            "revision": resource.revision,
                            "license_spdx": resource.license_spdx,
                            "artifact_file": component_resources[resource.resource_id][1],
                            "artifact_sha256": resource.artifact_sha256,
                            "copy_timeout_seconds": resource.copy_timeout_seconds,
                        }
                        for resource in component.launch.resources
                    ],
                }
            else:
                runtime_step["operations"] = adapter_by_id[step.subject_id].operations
            runtime_steps.append(runtime_step)

        runtime = {
            "schema_version": "1",
            "system_name": definition.name,
            "mode": definition.mode,
            "sandbox_resources": _primitive(definition.sandbox_resources),
            "sandbox_image": (
                _primitive(definition.sandbox_image) if definition.sandbox_image else None
            ),
            "external_input": definition.external_input,
            "required_output": definition.required_output,
            "contract_files": contract_files,
            "steps": runtime_steps,
        }
        _write_yaml(temporary / "runtime.yaml", runtime)
        _write_yaml(
            temporary / "blackridge.blueprint.yaml",
            {
                "schema_version": "1",
                "system_name": definition.name,
                "goal": definition.goal,
                "mode": definition.mode,
                "generation_ready": plan.complete,
                "execution_ready": definition.mode == "calibration",
                "release_ready": False,
                "external_input": definition.external_input,
                "required_output": definition.required_output,
                "steps": [_primitive(step) for step in plan.steps],
                "warnings": plan.warnings,
            },
        )
        _write_yaml(
            temporary / "evidence" / "evidence.lock.yaml",
            {
                "schema_version": "1",
                "components": [
                    {
                        "component_id": component.component_id,
                        "evidence": _primitive(component.evidence),
                    }
                    for component in selected_components
                ],
                "adapters": [
                    {
                        "adapter_id": adapter.adapter_id,
                        "evidence": _primitive(adapter.evidence),
                    }
                    for adapter in selected_adapters
                ],
            },
        )
        _write_yaml(
            temporary / "compose.yaml",
            {
                "services": {},
                "x-blackridge": {
                    "runtime": "runtime.yaml",
                    "status": "no-oci-services",
                    "reason": "v1 executes locked command-json components without a shell",
                },
            },
        )
        _write_text(
            temporary / "tests" / "README.md",
            "# System verification\n\n"
            "No L4 verdict is generated automatically. Run representative workloads, retain raw "
            "evidence, and add a named manual review here.",
        )
        _write_text(
            temporary / "sbom" / "README.md",
            "# SBOM gate\n\n"
            "The component lock is not an SBOM. Generate and review an SBOM for every distributed "
            "wheel, binary, or OCI image before release.",
        )
        _write_text(
            temporary / "README.md",
            f"# {definition.name}\n\n"
            f"{definition.goal}\n\n"
            "This bundle has a complete compatibility route, but it is not release-ready until "
            "representative L4 workloads receive named manual review.\n\n"
            "The locked component artifacts are included under `components/`; copy or move the "
            "whole bundle when executing it on another host.\n\n"
            "Run: `blackridge compose-run . INPUT.json --output OUTPUT.json "
            "--evidence evidence/run-probe.json --provenance-sha256 "
            "<trusted hash printed by compose-generate>`",
        )

        plan_hash = _sha256_file(temporary / "composition.plan.json")
        artifact_hashes = {
            path.relative_to(temporary).as_posix(): _sha256_file(path)
            for path in sorted(temporary.rglob("*"))
            if path.is_file()
        }
        provenance = {
            "schema_version": "1",
            "generated_at": (now or datetime.now(UTC)).astimezone(UTC).isoformat(),
            "generator": "blackridge-composer/1",
            "definition_file": str(definition_file.resolve()),
            "definition_sha256": plan.definition_sha256,
            "solver_module_sha256": plan.solver_module_sha256,
            "plan_sha256": plan_hash,
            "selected_components": plan.selected_component_ids,
            "selected_adapters": plan.selected_adapter_ids,
            "artifact_sha256": artifact_hashes,
            "release_ready": False,
        }
        _write_json(temporary / "provenance.json", provenance)
        artifact_hashes["provenance.json"] = _sha256_file(temporary / "provenance.json")
        temporary.replace(output_directory)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise

    return GeneratedSystem(
        output_directory=str(output_directory),
        definition_sha256=plan.definition_sha256,
        plan_sha256=plan_hash,
        execution_ready=definition.mode == "calibration",
        release_ready=False,
        artifact_sha256=artifact_hashes,
    )


def _validation_errors(schema: dict[str, object], value: object) -> list[dict[str, object]]:
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(value), key=lambda error: list(error.absolute_path))
    return [
        {
            "message": error.message,
            "instance_path": "/" + "/".join(str(part) for part in error.absolute_path),
            "schema_path": "/" + "/".join(str(part) for part in error.absolute_schema_path),
        }
        for error in errors
    ]


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    value = load_yaml(path)
    if not isinstance(value, dict):
        raise BlackridgeError(f"expected a mapping in {path}")
    return value


def _resolve_bundle_file(bundle: Path, relative: str) -> Path:
    resolved = (bundle / relative).resolve()
    try:
        resolved.relative_to(bundle.resolve())
    except ValueError as exc:
        raise BlackridgeError(f"generated bundle path escapes its root: {relative}") from exc
    if not resolved.is_file():
        raise BlackridgeError(f"generated bundle file is missing: {relative}")
    return resolved


def _resolve_bundle_directory(bundle: Path, relative: str) -> Path:
    resolved = (bundle / relative).resolve()
    try:
        resolved.relative_to(bundle.resolve())
    except ValueError as exc:
        raise BlackridgeError(f"generated bundle path escapes its root: {relative}") from exc
    if not resolved.is_dir():
        raise BlackridgeError(f"generated bundle directory is missing: {relative}")
    return resolved


def _resolve_component_launch(
    bundle: Path,
    launch: dict[str, Any],
    *,
    subject_id: str,
) -> dict[str, Any]:
    """Resolve a self-contained component launch without accepting host-bound paths."""

    artifact_value = launch.get("artifact_file")
    working_value = launch.get("working_directory")
    argv_value = launch.get("argv")
    resources_value = launch.get("resources", [])
    if (
        not isinstance(artifact_value, str)
        or not isinstance(working_value, str)
        or not isinstance(argv_value, list)
        or not argv_value
        or not all(isinstance(value, str) for value in argv_value)
        or not isinstance(resources_value, list)
    ):
        raise BlackridgeError(f"component launch control is invalid: {subject_id}")
    artifact = _resolve_bundle_file(bundle, artifact_value)
    working_directory = _resolve_bundle_directory(bundle, working_value)
    resolved_resources: list[dict[str, Any]] = []
    resource_paths: dict[str, Path] = {}
    for resource_value in resources_value:
        if not isinstance(resource_value, dict):
            raise BlackridgeError(f"component resource control is invalid: {subject_id}")
        resource_id = resource_value.get("resource_id")
        resource_file = resource_value.get("artifact_file")
        resource_hash = resource_value.get("artifact_sha256")
        if (
            not isinstance(resource_id, str)
            or resource_id in resource_paths
            or not isinstance(resource_file, str)
            or not isinstance(resource_hash, str)
        ):
            raise BlackridgeError(f"component resource control is invalid: {subject_id}")
        resource_path = _resolve_bundle_file(bundle, resource_file)
        if _sha256_file(resource_path) != resource_hash:
            raise BlackridgeError(
                f"component resource failed integrity: {subject_id}/{resource_id}"
            )
        resource_paths[resource_id] = resource_path
        resolved_resource = dict(resource_value)
        resolved_resource["artifact_file"] = str(resource_path)
        resolved_resources.append(resolved_resource)
    argv: list[str] = []
    for value in argv_value:
        if value == "{python}":
            argv.append(sys.executable)
        elif value == "{artifact}":
            argv.append(str(artifact))
        elif value.startswith("{resource:") and value.endswith("}"):
            resource_id = value.removeprefix("{resource:").removesuffix("}")
            if resource_id not in resource_paths:
                raise BlackridgeError(
                    f"component launch references an unknown resource: {subject_id}"
                )
            argv.append(str(resource_paths[resource_id]))
        elif "{python}" in value or "{artifact}" in value or "{resource:" in value:
            raise BlackridgeError(
                f"component launch marker is not a complete argument: {subject_id}"
            )
        elif Path(value).is_absolute():
            raise BlackridgeError(f"generated component has a host-bound argv path: {subject_id}")
        else:
            argv.append(value)
    if str(artifact) not in argv:
        raise BlackridgeError(
            f"generated component argv does not reference its bundled artifact: {subject_id}"
        )
    for resource_id, resource_path in resource_paths.items():
        if str(resource_path) not in argv:
            raise BlackridgeError(
                f"generated component argv does not reference resource {resource_id}: {subject_id}"
            )
    resolved = dict(launch)
    resolved.update(
        {
            "argv": argv,
            "working_directory": str(working_directory),
            "artifact_file": str(artifact),
            "resources": resolved_resources,
        }
    )
    return resolved


def _validate_runtime_consistency(bundle: Path, runtime: dict[str, Any]) -> None:
    """Reject a hash-valid bundle whose generated control files disagree semantically."""

    definition_file = _resolve_bundle_file(bundle, "composition.definition.yaml")
    definition = _load_yaml_mapping(definition_file)
    plan_value = json.loads(
        _resolve_bundle_file(bundle, "composition.plan.json").read_text(encoding="utf-8")
    )
    component_locks = _load_yaml_mapping(_resolve_bundle_file(bundle, "components.lock.yaml"))
    if not isinstance(plan_value, dict):
        raise BlackridgeError("generated composition plan is invalid")
    if plan_value.get("definition_sha256") != _sha256_file(definition_file):
        raise BlackridgeError("generated plan disagrees with its frozen definition hash")
    for runtime_key, definition_key in (
        ("system_name", "name"),
        ("mode", "mode"),
        ("external_input", "external_input"),
        ("required_output", "required_output"),
    ):
        if runtime.get(runtime_key) != definition.get(definition_key):
            raise BlackridgeError(f"generated runtime disagrees with its definition: {runtime_key}")
    default_sandbox_resources = _primitive(SandboxResources())
    definition_sandbox_resources = definition.get(
        "sandbox_resources", default_sandbox_resources
    )
    if runtime.get("sandbox_resources") != definition_sandbox_resources:
        raise BlackridgeError(
            "generated runtime disagrees with its definition: sandbox_resources"
        )
    if runtime.get("sandbox_image") != definition.get("sandbox_image"):
        raise BlackridgeError("generated runtime disagrees with its definition: sandbox_image")
    if runtime.get("system_name") != plan_value.get("system_name") or runtime.get(
        "mode"
    ) != plan_value.get("mode"):
        raise BlackridgeError("generated runtime disagrees with its composition plan")

    definition_contracts_value = definition.get("contracts")
    runtime_contracts = runtime.get("contract_files")
    if not isinstance(definition_contracts_value, list) or not isinstance(runtime_contracts, dict):
        raise BlackridgeError("generated runtime or definition contracts are invalid")
    definition_contracts: dict[str, object] = {}
    for contract in definition_contracts_value:
        if (
            not isinstance(contract, dict)
            or not isinstance(contract.get("contract_id"), str)
            or not isinstance(contract.get("schema"), dict)
        ):
            raise BlackridgeError("generated definition contract is invalid")
        definition_contracts[contract["contract_id"]] = contract["schema"]
    if len(definition_contracts) != len(definition_contracts_value) or set(
        runtime_contracts
    ) != set(definition_contracts):
        raise BlackridgeError("generated runtime contract inventory disagrees with definition")
    for contract_id, relative in runtime_contracts.items():
        if not isinstance(relative, str):
            raise BlackridgeError("generated runtime contract path is invalid")
        actual_schema = json.loads(
            _resolve_bundle_file(bundle, relative).read_text(encoding="utf-8")
        )
        if actual_schema != definition_contracts[contract_id]:
            raise BlackridgeError(
                f"generated runtime contract disagrees with definition: {contract_id}"
            )

    runtime_steps = runtime.get("steps")
    plan_steps = plan_value.get("steps")
    if not isinstance(runtime_steps, list) or not isinstance(plan_steps, list):
        raise BlackridgeError("generated runtime or plan steps are invalid")
    step_keys = {
        "index",
        "step_type",
        "subject_id",
        "input_contract",
        "additional_input_contracts",
        "output_contract",
    }
    if len(runtime_steps) != len(plan_steps):
        raise BlackridgeError("generated runtime step count disagrees with its plan")
    for runtime_step, plan_step in zip(runtime_steps, plan_steps, strict=True):
        if not isinstance(runtime_step, dict) or not isinstance(plan_step, dict):
            raise BlackridgeError("generated runtime or plan step is invalid")
        if {key: runtime_step.get(key) for key in step_keys} != {
            key: plan_step.get(key) for key in step_keys
        }:
            raise BlackridgeError("generated runtime step disagrees with its plan")

    locked_components_value = component_locks.get("components")
    if not isinstance(locked_components_value, list) or not all(
        isinstance(item, dict) and isinstance(item.get("component_id"), str)
        for item in locked_components_value
    ):
        raise BlackridgeError("generated component lock is invalid")
    locked_components = {item.get("component_id"): item for item in locked_components_value}
    selected_components = plan_value.get("selected_component_ids")
    if (
        len(locked_components) != len(locked_components_value)
        or not isinstance(selected_components, list)
        or not all(isinstance(item, str) for item in selected_components)
        or set(locked_components) != set(selected_components)
    ):
        raise BlackridgeError("generated component lock disagrees with its plan")
    selected_adapters = plan_value.get("selected_adapter_ids")
    runtime_adapter_ids = {
        step.get("subject_id")
        for step in runtime_steps
        if isinstance(step, dict) and step.get("step_type") == "adapter"
    }
    if (
        not isinstance(selected_adapters, list)
        or not all(isinstance(item, str) for item in selected_adapters)
        or runtime_adapter_ids != set(selected_adapters)
    ):
        raise BlackridgeError("generated adapter inventory disagrees with its plan")

    for runtime_step in runtime_steps:
        subject_id = runtime_step.get("subject_id")
        if not isinstance(subject_id, str):
            raise BlackridgeError("generated runtime step has no subject id")
        if runtime_step.get("step_type") == "component":
            locked = locked_components.get(subject_id)
            launch = runtime_step.get("launch")
            if not isinstance(locked, dict) or not isinstance(launch, dict):
                raise BlackridgeError(f"generated component lock is missing: {subject_id}")
            locked_launch = locked.get("launch")
            if not isinstance(locked_launch, dict):
                raise BlackridgeError(f"generated component launch lock is missing: {subject_id}")
            additional_inputs = runtime_step.get("additional_input_contracts", [])
            if not isinstance(additional_inputs, list) or not all(
                isinstance(item, str) for item in additional_inputs
            ):
                raise BlackridgeError(
                    f"generated component input contracts are invalid: {subject_id}"
                )
            runtime_inputs = [runtime_step.get("input_contract"), *additional_inputs]
            if locked.get("accepts") != runtime_inputs or locked.get("produces") != [
                runtime_step.get("output_contract")
            ]:
                raise BlackridgeError(
                    f"generated component contracts disagree with their lock: {subject_id}"
                )
            locked_resources = locked_launch.get("resources", [])
            runtime_resources = launch.get("resources", [])
            if not isinstance(locked_resources, list) or not isinstance(runtime_resources, list):
                raise BlackridgeError(f"generated component resources are invalid: {subject_id}")
            resource_fields = {
                "resource_id",
                "source_uri",
                "revision",
                "license_spdx",
                "artifact_sha256",
                "copy_timeout_seconds",
            }
            locked_resource_controls = [
                {field: resource.get(field) for field in resource_fields}
                for resource in locked_resources
                if isinstance(resource, dict)
            ]
            runtime_resource_controls = [
                {field: resource.get(field) for field in resource_fields}
                for resource in runtime_resources
                if isinstance(resource, dict)
            ]
            if (
                len(locked_resource_controls) != len(locked_resources)
                or len(runtime_resource_controls) != len(runtime_resources)
                or locked_resource_controls != runtime_resource_controls
            ):
                raise BlackridgeError(
                    f"generated component resources disagree with their lock: {subject_id}"
                )
            for field in ("artifact_sha256", "timeout_seconds", "environment_allowlist"):
                if launch.get(field) != locked_launch.get(field):
                    raise BlackridgeError(
                        f"generated component launch disagrees with its lock: {subject_id}"
                    )
        elif runtime_step.get("step_type") == "adapter":
            manifest = _load_yaml_mapping(
                _resolve_bundle_file(bundle, f"adapters/{subject_id}.yaml")
            )
            operations = runtime_step.get("operations")
            if (
                manifest.get("adapter_id") != subject_id
                or manifest.get("source_contract") != runtime_step.get("input_contract")
                or runtime_step.get("additional_input_contracts", []) != []
                or manifest.get("target_contract") != runtime_step.get("output_contract")
                or manifest.get("operations") != operations
                or manifest.get("operations_sha256") != _sha256_json(operations)
            ):
                raise BlackridgeError(
                    f"generated adapter runtime disagrees with its lock: {subject_id}"
                )
        else:
            raise BlackridgeError("generated runtime contains an unsupported step type")


def _verify_provenance_root(provenance_file: Path, expected_sha256: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{64}", expected_sha256):
        raise BlackridgeError("expected provenance SHA-256 must be 64 lowercase hex characters")
    actual = _sha256_file(provenance_file)
    if actual != expected_sha256:
        raise BlackridgeError(
            "generated provenance root hash does not match the externally supplied SHA-256"
        )
    return actual


def _host_component_process(
    _subject_id: str,
    launch: dict[str, Any],
    artifact: object,
) -> dict[str, object]:
    argv = launch["argv"]
    cwd = launch["working_directory"]
    allowlist = launch.get("environment_allowlist", [])
    timeout = launch["timeout_seconds"]
    environment = {
        key: os.environ[key] for key in allowlist if isinstance(key, str) and key in os.environ
    }
    with tempfile.TemporaryDirectory(prefix="blackridge-component-") as scratch:
        # Give libraries a writable, per-process home/cache without exposing the
        # host user's profile.  A fixed synthetic identity also keeps Windows'
        # getpass fallback from attempting to import the Unix-only ``pwd`` module.
        environment.update(
            {
                "HOME": scratch,
                "TEMP": scratch,
                "TMP": scratch,
                "TMPDIR": scratch,
                "USER": "blackridge",
                "USERNAME": "blackridge",
                "PYTHONIOENCODING": "utf-8",
            }
        )
        if os.name == "nt" and "SYSTEMROOT" in os.environ:
            # CPython and native extensions use the Windows system directory to
            # initialize core providers even when no user environment is forwarded.
            system_root = Path(os.environ["SYSTEMROOT"])
            environment["SYSTEMROOT"] = str(system_root)
            environment["USERPROFILE"] = scratch
            environment["PATH"] = os.pathsep.join(
                [
                    str(Path(sys.executable).parent),
                    str(system_root / "System32"),
                    str(system_root),
                ]
            )
        completed = run_bounded(
            argv,
            input_text=json.dumps(artifact),
            cwd=cwd,
            env=environment,
            timeout_seconds=float(timeout),
        )
    return {
        "executor": "host-shell-free",
        "argv": argv,
        "working_directory": cwd,
        "environment_names": sorted(environment),
        "duration_seconds": completed.duration_seconds,
        "timed_out": completed.timed_out,
        "output_limit_exceeded": completed.output_limit_exceeded,
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "stdout_bytes_seen": completed.stdout_bytes_seen,
        "stderr_bytes_seen": completed.stderr_bytes_seen,
    }


def run_generated_system(
    bundle_directory: Path,
    input_artifact: object,
    *,
    expected_provenance_sha256: str,
    _component_process: Callable[
        [str, dict[str, Any], object], dict[str, object]
    ] = _host_component_process,
    _provider: str = "blackridge-generated-graph-runtime/1",
    _runtime_mode: Literal["calibration"] = "calibration",
) -> ProbeEvidence:
    """Execute a generated calibration bundle and retain every boundary observation."""

    bundle = bundle_directory.resolve()
    provenance_file = _resolve_bundle_file(bundle, "provenance.json")
    verified_provenance_sha256 = _verify_provenance_root(
        provenance_file, expected_provenance_sha256
    )
    provenance = json.loads(provenance_file.read_text(encoding="utf-8"))
    if not isinstance(provenance, dict) or not isinstance(provenance.get("artifact_sha256"), dict):
        raise BlackridgeError("generated provenance manifest is invalid")
    integrity_mismatches: list[dict[str, Any]] = []
    for relative, expected_hash in provenance["artifact_sha256"].items():
        if not isinstance(relative, str) or not isinstance(expected_hash, str):
            raise BlackridgeError("generated provenance artifact mapping is invalid")
        artifact_path = _resolve_bundle_file(bundle, relative)
        actual_hash = _sha256_file(artifact_path)
        if actual_hash != expected_hash:
            integrity_mismatches.append(
                {
                    "file": relative,
                    "expected_sha256": expected_hash,
                    "actual_sha256": actual_hash,
                }
            )
    if integrity_mismatches:
        raise BlackridgeError(
            "generated bundle integrity failed: "
            + ", ".join(item["file"] for item in integrity_mismatches)
        )
    runtime_file = _resolve_bundle_file(bundle, "runtime.yaml")
    runtime = _load_yaml_mapping(runtime_file)
    if runtime.get("mode") != _runtime_mode:
        raise BlackridgeError("generated runtime mode is not enabled by this execution backend")
    _validate_runtime_consistency(bundle, runtime)
    contract_files = runtime.get("contract_files")
    steps = runtime.get("steps")
    if not isinstance(contract_files, dict) or not isinstance(steps, list):
        raise BlackridgeError("generated runtime is missing contract files or steps")
    schemas: dict[str, dict[str, Any]] = {}
    for contract_id, relative in contract_files.items():
        if not isinstance(contract_id, str) or not isinstance(relative, str):
            raise BlackridgeError("generated runtime contract mapping is invalid")
        schema = json.loads(_resolve_bundle_file(bundle, relative).read_text(encoding="utf-8"))
        if not isinstance(schema, dict):
            raise BlackridgeError(f"contract schema is not an object: {contract_id}")
        Draft202012Validator.check_schema(schema)
        schemas[contract_id] = schema

    validated_steps: list[tuple[dict[str, Any], str, str, list[str], str]] = []
    component_controls: dict[int, tuple[dict[str, Any], Path, str, dict[str, object]]] = {}
    component_integrity_failures: dict[int, str] = {}
    for step_index, raw_step in enumerate(steps):
        if not isinstance(raw_step, dict):
            raise BlackridgeError("generated runtime step is not an object")
        subject_id = raw_step.get("subject_id")
        step_type = raw_step.get("step_type")
        input_contract = raw_step.get("input_contract")
        additional_input_contracts = raw_step.get("additional_input_contracts", [])
        output_contract = raw_step.get("output_contract")
        if not (
            isinstance(subject_id, str)
            and isinstance(step_type, str)
            and isinstance(input_contract, str)
            and isinstance(additional_input_contracts, list)
            and all(isinstance(item, str) for item in additional_input_contracts)
            and isinstance(output_contract, str)
        ):
            raise BlackridgeError("generated runtime step fields are invalid")
        input_contracts = [input_contract, *additional_input_contracts]
        if len(input_contracts) != len(set(input_contracts)):
            raise BlackridgeError("generated runtime step repeats an input contract")
        if step_type == "adapter" and additional_input_contracts:
            raise BlackridgeError("generated adapter runtime has multiple input contracts")
        validated_steps.append((raw_step, subject_id, step_type, input_contracts, output_contract))
        if step_type != "component":
            continue
        launch = raw_step.get("launch")
        if not isinstance(launch, dict):
            raise BlackridgeError(f"component step has no launch control: {subject_id}")
        launch = _resolve_component_launch(bundle, launch, subject_id=subject_id)
        raw_step["launch"] = launch
        argv = launch.get("argv")
        cwd = launch.get("working_directory")
        timeout = launch.get("timeout_seconds")
        allowlist = launch.get("environment_allowlist", [])
        artifact_file = launch.get("artifact_file")
        artifact_hash = launch.get("artifact_sha256")
        if (
            not isinstance(argv, list)
            or not argv
            or not all(isinstance(value, str) for value in argv)
            or not isinstance(cwd, str)
            or not isinstance(timeout, (int, float))
            or not isinstance(allowlist, list)
            or not isinstance(artifact_file, str)
            or not isinstance(artifact_hash, str)
        ):
            raise BlackridgeError(f"component launch control is invalid: {subject_id}")
        launch_artifact = Path(artifact_file).resolve()
        actual_artifact_hash = _sha256_file(launch_artifact) if launch_artifact.is_file() else None
        integrity: dict[str, object] = {
            "file": str(launch_artifact),
            "exists": launch_artifact.is_file(),
            "expected_sha256": artifact_hash,
            "actual_sha256": actual_artifact_hash,
            "matches": actual_artifact_hash == artifact_hash,
        }
        component_controls[step_index] = (
            launch,
            launch_artifact,
            artifact_hash,
            integrity,
        )
        if actual_artifact_hash != artifact_hash:
            component_integrity_failures[step_index] = (
                f"component {subject_id} launch artifact failed integrity"
            )

    external_input = runtime.get("external_input")
    required_output = runtime.get("required_output")
    if not isinstance(external_input, str) or not isinstance(required_output, str):
        raise BlackridgeError("generated runtime contract endpoints are invalid")
    artifacts_by_contract: dict[str, object] = {external_input: input_artifact}
    last_contract = external_input
    initial_errors = _validation_errors(schemas[external_input], input_artifact)
    step_observations: list[dict[str, object]] = []
    failed = bool(initial_errors) or bool(component_integrity_failures)
    if component_integrity_failures:
        failure_reason = next(iter(component_integrity_failures.values()))
    elif initial_errors:
        failure_reason = "initial artifact violates its public contract"
    else:
        failure_reason = None

    for step_index, (
        raw_step,
        subject_id,
        step_type,
        input_contracts,
        output_contract,
    ) in enumerate(validated_steps):
        if component_integrity_failures:
            if step_index in component_integrity_failures:
                step_observations.append(
                    {
                        "subject_id": subject_id,
                        "step_type": step_type,
                        "status": "failed",
                        "artifact_integrity": component_controls[step_index][3],
                        "reason": component_integrity_failures[step_index],
                    }
                )
            else:
                step_observations.append(
                    {
                        "subject_id": subject_id,
                        "step_type": step_type,
                        "status": "skipped",
                        "reason": "component launch artifact preflight failed",
                    }
                )
            continue
        if failed:
            step_observations.append(
                {
                    "subject_id": subject_id,
                    "step_type": step_type,
                    "status": "skipped",
                    "reason": "an earlier boundary failed",
                }
            )
            continue
        missing_inputs = [
            contract_id
            for contract_id in input_contracts
            if contract_id not in artifacts_by_contract
        ]
        if missing_inputs:
            failed = True
            failure_reason = (
                f"step {subject_id} is missing input contracts: {', '.join(missing_inputs)}"
            )
            step_observations.append(
                {
                    "subject_id": subject_id,
                    "step_type": step_type,
                    "status": "failed",
                    "reason": failure_reason,
                }
            )
            continue
        before_errors = {
            contract_id: errors
            for contract_id in input_contracts
            if (
                errors := _validation_errors(
                    schemas[contract_id], artifacts_by_contract[contract_id]
                )
            )
        }
        if before_errors:
            failed = True
            failure_reason = f"step {subject_id} received an invalid input artifact"
            step_observations.append(
                {
                    "subject_id": subject_id,
                    "step_type": step_type,
                    "status": "failed",
                    "input_validation_errors": before_errors,
                    "reason": failure_reason,
                }
            )
            continue

        artifact = (
            artifacts_by_contract[input_contracts[0]]
            if len(input_contracts) == 1
            else {
                "inputs": {
                    contract_id: artifacts_by_contract[contract_id]
                    for contract_id in input_contracts
                }
            }
        )

        observation: dict[str, Any] = {
            "subject_id": subject_id,
            "step_type": step_type,
            "status": "failed",
            "input_contract": input_contracts[0],
            "additional_input_contracts": input_contracts[1:],
            "output_contract": output_contract,
            "input_artifact": artifact,
        }
        produced: object | None = None
        artifact_integrity_failed = False
        if step_type == "adapter":
            operations = raw_step.get("operations")
            observation["operations"] = deepcopy(operations)
            try:
                produced = jsonpatch.JsonPatch(deepcopy(operations)).apply(artifact, in_place=False)
                observation["patch_error"] = None
            except (jsonpatch.JsonPatchException, TypeError) as exc:
                observation["patch_error"] = f"{type(exc).__name__}: {exc}"
                failure_reason = f"adapter {subject_id} failed"
        elif step_type == "component":
            launch, launch_artifact, artifact_hash, integrity = component_controls[step_index]
            observation["artifact_integrity"] = dict(integrity)
            process = _component_process(subject_id, launch, artifact)
            required_process_fields = {
                "argv",
                "working_directory",
                "environment_names",
                "duration_seconds",
                "timed_out",
                "output_limit_exceeded",
                "exit_code",
                "stdout",
                "stderr",
            }
            if not required_process_fields <= process.keys():
                raise BlackridgeError(
                    f"component executor returned incomplete evidence: {subject_id}"
                )
            observation["process"] = process
            if process["timed_out"]:
                failure_reason = f"component {subject_id} timed out"
            elif process["output_limit_exceeded"]:
                failure_reason = f"component {subject_id} exceeded the output limit"
            elif process["exit_code"] == 0:
                try:
                    produced = json.loads(str(process["stdout"]))
                except json.JSONDecodeError as exc:
                    observation["parse_error"] = f"JSONDecodeError: {exc}"
                    failure_reason = f"component {subject_id} emitted invalid JSON"
            else:
                failure_reason = f"component {subject_id} exited nonzero"
            after_artifact_hash = (
                _sha256_file(launch_artifact) if launch_artifact.is_file() else None
            )
            observation["artifact_integrity"]["after_sha256"] = after_artifact_hash
            observation["artifact_integrity"]["unchanged_after_execution"] = (
                after_artifact_hash == artifact_hash
            )
            artifact_integrity_failed = after_artifact_hash != artifact_hash
            if artifact_integrity_failed:
                failure_reason = f"component {subject_id} changed its locked launch artifact"
        else:
            raise BlackridgeError(f"unsupported generated runtime step: {step_type}")

        if produced is not None:
            after_errors = _validation_errors(schemas[output_contract], produced)
            observation["output_artifact"] = produced
            observation["output_validation_errors"] = after_errors
            observation["output_contract_valid"] = not after_errors
            if not after_errors and not artifact_integrity_failed:
                observation["status"] = "completed"
                artifacts_by_contract[output_contract] = produced
                last_contract = output_contract
            elif after_errors:
                failure_reason = f"step {subject_id} produced an invalid output artifact"
        if observation["status"] != "completed":
            failed = True
            observation["reason"] = failure_reason
        step_observations.append(observation)

    final_artifact = artifacts_by_contract.get(required_output)
    final_errors = (
        _validation_errors(schemas[required_output], final_artifact)
        if required_output in artifacts_by_contract
        else []
    )
    completed = not failed and required_output in artifacts_by_contract and not final_errors
    warnings: list[str] = []
    if not completed:
        warnings.append("The generated system did not produce a valid required output artifact.")
    return ProbeEvidence(
        probe_id=uuid4().hex,
        observed_at=datetime.now(UTC),
        provider=_provider,
        subject=str(runtime.get("system_name")),
        request={
            "bundle_directory": str(bundle),
            "runtime_sha256": _sha256_file(runtime_file),
            "runtime_module_sha256": _sha256_file(Path(__file__)),
            "provenance_sha256": verified_provenance_sha256,
            "expected_provenance_sha256": expected_provenance_sha256,
            "bundle_integrity_mismatches": integrity_mismatches,
            "input_artifact": input_artifact,
        },
        observations={
            "probe_completed": True,
            "runtime_mode": runtime.get("mode"),
            "initial_contract": runtime.get("external_input"),
            "initial_validation_errors": initial_errors,
            "steps": step_observations,
            "all_steps_completed": completed,
            "failure_reason": failure_reason,
            "final_contract": required_output
            if required_output in artifacts_by_contract
            else last_contract,
            "available_contracts": sorted(artifacts_by_contract),
            "required_output": required_output,
            "final_artifact": (
                final_artifact
                if required_output in artifacts_by_contract
                else artifacts_by_contract[last_contract]
            ),
            "final_validation_errors": final_errors,
        },
        sources=[COMPOSITION_SOURCE, JSON_PATCH_SOURCE, JSON_SCHEMA_SOURCE],
        warnings=warnings,
    )


def _sandbox_component_argv(
    declared_argv: Sequence[object],
    *,
    artifact_file: str,
    container_artifact: str,
    resource_files: dict[str, str] | None = None,
    subject_id: str,
) -> list[str]:
    """Map one locked Python component launch without trusting host absolute paths."""

    effective_argv: list[str] = []
    resource_files = resource_files or {}
    python_names = {"python", "python.exe", "python3", "python3.exe"}
    for index, value in enumerate(declared_argv):
        argument = str(value)
        if index == 0 and Path(argument).name.lower() in python_names:
            effective_argv.append("python")
        elif str(Path(argument).resolve()) == artifact_file:
            effective_argv.append(container_artifact)
        elif str(Path(argument).resolve()) in resource_files:
            effective_argv.append(resource_files[str(Path(argument).resolve())])
        elif Path(argument).is_absolute():
            raise BlackridgeError(
                f"sandboxed component has an unmapped absolute argv path: {subject_id}"
            )
        else:
            effective_argv.append(argument)
    if container_artifact not in effective_argv:
        raise BlackridgeError(
            f"sandboxed component argv does not reference its locked artifact: {subject_id}"
        )
    return effective_argv


_SANDBOX_PREFLIGHT_CODE = """\
import json
import os
import pathlib
import socket
import sys


def write_probe(path, payload):
    try:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(payload)
        return {"allowed": True, "errno": None}
    except OSError as exc:
        return {"allowed": False, "errno": exc.errno}


def connect_probe(target):
    try:
        connection = socket.create_connection(target, timeout=2)
        connection.close()
        return True
    except OSError:
        return False


status = dict(
    line.split(":", 1)
    for line in pathlib.Path("/proc/self/status").read_text(encoding="utf-8").splitlines()
    if ":" in line
)
cgroup_paths = {
    "memory_max": "/sys/fs/cgroup/memory.max",
    "memory_swap_max": "/sys/fs/cgroup/memory.swap.max",
    "pids_max": "/sys/fs/cgroup/pids.max",
    "cpu_max": "/sys/fs/cgroup/cpu.max",
}
scratch = pathlib.Path("/tmp/blackridge-preflight-write")
etc_probe = pathlib.Path("/etc/blackridge-preflight-write")
result = {
    "uid": os.getuid(),
    "gid": os.getgid(),
    "cap_eff": status.get("CapEff", "").strip(),
    "no_new_privs": status.get("NoNewPrivs", "").strip(),
    "component_write": write_probe(pathlib.Path(sys.argv[1]), ""),
    "etc_write": write_probe(etc_probe, "x"),
    "scratch_write": write_probe(scratch, "x"),
    "direct": connect_probe(("1.1.1.1", 443)),
    "dns": connect_probe(("pypi.org", 443)),
    "sensitive_environment_names": sorted(
        key
        for key in os.environ
        if any(
            needle in key.upper()
            for needle in ("DEEPSEEK", "GITHUB", "OPENAI", "TOKEN", "SECRET", "API_KEY")
        )
    ),
    "cgroup": {
        name: pathlib.Path(path).read_text(encoding="utf-8").strip()
        if pathlib.Path(path).is_file()
        else "unavailable"
        for name, path in cgroup_paths.items()
    },
}
for path in (scratch, etc_probe):
    try:
        path.unlink()
    except OSError:
        pass
print(json.dumps(result, sort_keys=True))
"""


def run_generated_system_sandboxed(
    bundle_directory: Path,
    input_artifact: object,
    *,
    expected_provenance_sha256: str,
    image_ref: str | None = None,
) -> ProbeEvidence:
    """Run a calibration bundle with copied components and a networkless Docker workload."""

    from blackridge.sandbox import (
        SWEREX_SOURCE,
        SwerexDockerProbe,
        _container_exists,
        inspect_local_image,
    )

    bundle = bundle_directory.resolve()
    provenance_file = _resolve_bundle_file(bundle, "provenance.json")
    _verify_provenance_root(provenance_file, expected_provenance_sha256)
    provenance = json.loads(provenance_file.read_text(encoding="utf-8"))
    if not isinstance(provenance, dict) or not isinstance(provenance.get("artifact_sha256"), dict):
        raise BlackridgeError("generated provenance manifest is invalid")
    for relative, expected_hash in provenance["artifact_sha256"].items():
        if not isinstance(relative, str) or not isinstance(expected_hash, str):
            raise BlackridgeError("generated provenance artifact mapping is invalid")
        if _sha256_file(_resolve_bundle_file(bundle, relative)) != expected_hash:
            raise BlackridgeError(f"generated bundle integrity failed: {relative}")

    runtime_file = _resolve_bundle_file(bundle, "runtime.yaml")
    runtime = _load_yaml_mapping(runtime_file)
    if runtime.get("mode") != "calibration":
        raise BlackridgeError(
            "sandboxed generated-system v1 remains calibration-only pending hostile controls"
        )
    _validate_runtime_consistency(bundle, runtime)
    try:
        sandbox_resources = SandboxResources.model_validate(runtime.get("sandbox_resources"))
    except ValueError as exc:
        raise BlackridgeError("generated sandbox resource control is invalid") from exc
    locked_image_value = runtime.get("sandbox_image")
    locked_image: SandboxImage | None = None
    if locked_image_value is not None:
        try:
            locked_image = SandboxImage.model_validate(locked_image_value)
        except ValueError as exc:
            raise BlackridgeError("generated sandbox image control is invalid") from exc
        if image_ref is not None and image_ref != locked_image.reference:
            raise BlackridgeError("requested sandbox image disagrees with the generated image lock")
        effective_image_ref = locked_image.reference
    else:
        effective_image_ref = image_ref or "blackridge/swerex-runtime:1.4.0"
    memory_flag = f"{sandbox_resources.memory_mb}m"
    cpu_flag = f"{sandbox_resources.cpus:g}"
    expected_memory_bytes = str(sandbox_resources.memory_mb * 1024 * 1024)
    expected_cpu_max = f"{round(sandbox_resources.cpus * 100_000)} 100000"
    steps = runtime.get("steps")
    if not isinstance(steps, list):
        raise BlackridgeError("generated runtime is missing steps")
    component_controls: list[tuple[str, dict[str, object]]] = []
    for raw_step in steps:
        if not isinstance(raw_step, dict) or raw_step.get("step_type") != "component":
            continue
        subject_id = raw_step.get("subject_id")
        launch = raw_step.get("launch")
        if not isinstance(subject_id, str) or not isinstance(launch, dict):
            raise BlackridgeError("generated component launch control is invalid")
        launch = _resolve_component_launch(bundle, launch, subject_id=subject_id)
        artifact_file = launch.get("artifact_file")
        artifact_hash = launch.get("artifact_sha256")
        allowlist = launch.get("environment_allowlist", [])
        if not isinstance(artifact_file, str) or not isinstance(artifact_hash, str):
            raise BlackridgeError(f"component launch artifact is invalid: {subject_id}")
        if allowlist != []:
            raise BlackridgeError(
                f"sandboxed generated-system v1 forwards no component environment: {subject_id}"
            )
        artifact_path = Path(artifact_file).resolve()
        if not artifact_path.is_file() or _sha256_file(artifact_path) != artifact_hash:
            raise BlackridgeError(f"component launch artifact failed integrity: {subject_id}")
        component_controls.append((subject_id, launch))
    if not component_controls:
        raise BlackridgeError("sandboxed generated-system v1 requires a component step")

    adapter = SwerexDockerProbe()
    DockerDeployment, Command = adapter._runtime_types()
    image = inspect_local_image(effective_image_ref)
    if locked_image is not None and image["resolved_id"] != locked_image.expected_id:
        raise BlackridgeError("resolved sandbox image ID disagrees with the generated image lock")
    deployment = DockerDeployment(
        image=image["resolved_id"],
        pull="never",
        remove_container=True,
        startup_timeout=180,
        docker_args=[
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            f"--pids-limit={sandbox_resources.pids}",
            f"--memory={memory_flag}",
            f"--memory-swap={memory_flag}",
            f"--cpus={cpu_flag}",
        ],
        logger=adapter._logger(),
    )
    container_name: str | None = None
    copied: list[dict[str, object]] = []
    container_artifacts: dict[str, str] = {}
    container_resources: dict[str, dict[str, str]] = {}
    boundary: dict[str, object] | None = None
    preflight: dict[str, object] | None = None
    force_remove: dict[str, object] | None = None
    container_exists_after: bool | None = None
    runtime_probe: ProbeEvidence | None = None

    async def start_and_prepare() -> None:
        nonlocal container_name
        await deployment.start()
        container_name = deployment.container_name
        response = await deployment.runtime.execute(
            Command(
                command=["mkdir", "-p", "/workspace/components", "/workspace/resources"],
                timeout=30,
                shell=False,
                check=False,
            )
        )
        if response.exit_code != 0:
            raise BlackridgeError("sandbox component directory preparation failed")

    def sandbox_component_process(
        subject_id: str,
        launch: dict[str, Any],
        artifact: object,
    ) -> dict[str, object]:
        if container_name is None:
            raise BlackridgeError("sandbox container is unavailable")
        declared_argv = launch["argv"]
        artifact_file = str(Path(str(launch["artifact_file"])).resolve())
        effective_argv = _sandbox_component_argv(
            declared_argv,
            artifact_file=artifact_file,
            container_artifact=container_artifacts[subject_id],
            resource_files=container_resources[subject_id],
            subject_id=subject_id,
        )
        argv = [
            "docker",
            "exec",
            "-i",
            "--user",
            "65534:65534",
            "--workdir",
            "/workspace/components",
            "--env",
            "PYTHONIOENCODING=utf-8",
            "--env",
            "HOME=/tmp",
            "--env",
            "TMPDIR=/tmp",
            container_name,
            "timeout",
            "--verbose",
            "--signal=TERM",
            "--kill-after=1s",
            f"{float(launch['timeout_seconds']):g}s",
            *effective_argv,
        ]
        completed = run_bounded(
            argv,
            input_text=json.dumps(artifact),
            timeout_seconds=float(launch["timeout_seconds"]) + 5,
        )
        timed_out = completed.timed_out or (
            completed.returncode in {124, 137}
            and completed.duration_seconds >= float(launch["timeout_seconds"]) * 0.9
        )
        return {
            "executor": "docker-exec-shell-free",
            "declared_argv": declared_argv,
            "argv": effective_argv,
            "container_argv": argv[argv.index(container_name) + 1 :],
            "working_directory": "/workspace/components",
            "user": "65534:65534",
            "environment_names": ["HOME", "PYTHONIOENCODING", "TMPDIR"],
            "duration_seconds": completed.duration_seconds,
            "timeout_enforcer": "coreutils-timeout-term-then-kill",
            "timed_out": timed_out,
            "output_limit_exceeded": completed.output_limit_exceeded,
            "exit_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "stdout_bytes_seen": completed.stdout_bytes_seen,
            "stderr_bytes_seen": completed.stderr_bytes_seen,
        }

    try:
        asyncio.run(start_and_prepare())
        if container_name is None:
            raise BlackridgeError("SWE-ReX did not provide a container name")
        for index, (subject_id, launch) in enumerate(component_controls, start=1):
            source = Path(str(launch["artifact_file"])).resolve()
            suffix = source.suffix if source.suffix.isascii() else ""
            target = f"/workspace/components/component-{index}{suffix}"
            copy_argv = ["docker", "cp", str(source), f"{container_name}:{target}"]
            copied_process = run_bounded(copy_argv, timeout_seconds=30)
            record: dict[str, object] = {
                "subject_id": subject_id,
                "argv": copy_argv,
                "exit_code": copied_process.returncode,
                "stderr": copied_process.stderr,
                "target": target,
            }
            copied.append(record)
            if copied_process.returncode != 0:
                raise BlackridgeError(f"component copy failed: {subject_id}")
            hash_process = run_bounded(
                ["docker", "exec", container_name, "sha256sum", target],
                timeout_seconds=15,
            )
            actual_hash = hash_process.stdout.split()[0] if hash_process.returncode == 0 else None
            record["expected_sha256"] = launch["artifact_sha256"]
            record["container_sha256"] = actual_hash
            record["hash_matches"] = actual_hash == launch["artifact_sha256"]
            if not record["hash_matches"]:
                raise BlackridgeError(f"copied component hash mismatch: {subject_id}")
            container_artifacts[subject_id] = target
            container_resources[subject_id] = {}
            resources = launch.get("resources", [])
            if not isinstance(resources, list):
                raise BlackridgeError(f"component resources are invalid: {subject_id}")
            for resource_index, resource in enumerate(resources, start=1):
                if not isinstance(resource, dict):
                    raise BlackridgeError(f"component resource is invalid: {subject_id}")
                resource_id = resource.get("resource_id")
                resource_file = resource.get("artifact_file")
                resource_hash = resource.get("artifact_sha256")
                resource_copy_timeout = resource.get("copy_timeout_seconds")
                if (
                    not isinstance(resource_id, str)
                    or not isinstance(resource_file, str)
                    or not isinstance(resource_hash, str)
                    or not isinstance(resource_copy_timeout, int)
                    or isinstance(resource_copy_timeout, bool)
                    or not 5 <= resource_copy_timeout <= 1800
                ):
                    raise BlackridgeError(f"component resource is invalid: {subject_id}")
                resource_source = Path(resource_file).resolve()
                resource_suffix = resource_source.suffix if resource_source.suffix.isascii() else ""
                resource_target = (
                    f"/workspace/resources/resource-{index}-{resource_index}{resource_suffix}"
                )
                resource_copy_argv = [
                    "docker",
                    "cp",
                    str(resource_source),
                    f"{container_name}:{resource_target}",
                ]
                resource_copy = run_bounded(
                    resource_copy_argv,
                    timeout_seconds=float(resource_copy_timeout),
                )
                resource_record: dict[str, object] = {
                    "subject_id": subject_id,
                    "resource_id": resource_id,
                    "argv": resource_copy_argv,
                    "exit_code": resource_copy.returncode,
                    "stderr": resource_copy.stderr,
                    "target": resource_target,
                    "expected_sha256": resource_hash,
                    "timeout_seconds": resource_copy_timeout,
                }
                copied.append(resource_record)
                if resource_copy.returncode != 0:
                    raise BlackridgeError(
                        f"component resource copy failed: {subject_id}/{resource_id}"
                    )
                resource_hash_process = run_bounded(
                    ["docker", "exec", container_name, "sha256sum", resource_target],
                    timeout_seconds=15,
                )
                actual_resource_hash = (
                    resource_hash_process.stdout.split()[0]
                    if resource_hash_process.returncode == 0
                    else None
                )
                resource_record["container_sha256"] = actual_resource_hash
                resource_record["hash_matches"] = actual_resource_hash == resource_hash
                if not resource_record["hash_matches"]:
                    raise BlackridgeError(
                        f"copied component resource hash mismatch: {subject_id}/{resource_id}"
                    )
                container_resources[subject_id][str(resource_source)] = resource_target

        boundary = adapter._isolate_execution_network(container_name)
        if not boundary["applied"]:
            raise BlackridgeError(str(boundary["error"]))
        preflight_argv = [
            "docker",
            "exec",
            "--user",
            "65534:65534",
            "--env",
            "HOME=/tmp",
            "--env",
            "TMPDIR=/tmp",
            container_name,
            "python",
            "-c",
            _SANDBOX_PREFLIGHT_CODE,
            container_artifacts[component_controls[0][0]],
        ]
        preflight_process = run_bounded(
            preflight_argv,
            timeout_seconds=15,
        )
        preflight = {
            "argv": preflight_argv,
            "exit_code": preflight_process.returncode,
            "stdout": preflight_process.stdout,
            "stderr": preflight_process.stderr,
            "result": (
                json.loads(preflight_process.stdout) if preflight_process.returncode == 0 else None
            ),
        }
        preflight_result = preflight["result"]
        if not isinstance(preflight_result, dict):
            raise BlackridgeError("sandbox preflight did not return a JSON object")
        cgroup = preflight_result.get("cgroup")
        component_write = preflight_result.get("component_write")
        etc_write = preflight_result.get("etc_write")
        scratch_write = preflight_result.get("scratch_write")
        preflight_checks = {
            "non_root_user": preflight_result.get("uid") != 0 and preflight_result.get("gid") != 0,
            "no_effective_capabilities": preflight_result.get("cap_eff") == "0000000000000000",
            "no_new_privileges": preflight_result.get("no_new_privs") == "1",
            "component_write_denied": isinstance(component_write, dict)
            and component_write.get("allowed") is False,
            "etc_write_denied": isinstance(etc_write, dict) and etc_write.get("allowed") is False,
            "scratch_write_allowed": isinstance(scratch_write, dict)
            and scratch_write.get("allowed") is True,
            "direct_egress_denied": preflight_result.get("direct") is False,
            "dns_egress_denied": preflight_result.get("dns") is False,
            "sensitive_names_absent": preflight_result.get("sensitive_environment_names") == [],
            "memory_limit_exact": isinstance(cgroup, dict)
            and cgroup.get("memory_max") == expected_memory_bytes,
            "memory_swap_disabled": isinstance(cgroup, dict)
            and cgroup.get("memory_swap_max") == "0",
            "pids_limit_exact": isinstance(cgroup, dict)
            and cgroup.get("pids_max") == str(sandbox_resources.pids),
            "cpu_limit_exact": isinstance(cgroup, dict)
            and cgroup.get("cpu_max") == expected_cpu_max,
        }
        preflight["checks"] = preflight_checks
        preflight["passed"] = all(preflight_checks.values())
        if not preflight["passed"]:
            raise BlackridgeError("sandbox hostile-control preflight did not pass every check")
        runtime_probe = run_generated_system(
            bundle,
            input_artifact,
            expected_provenance_sha256=expected_provenance_sha256,
            _component_process=sandbox_component_process,
            _provider="blackridge-generated-sandbox-runtime/1",
        )
        post_execution_integrity: list[dict[str, object]] = []
        for subject_id, launch in component_controls:
            target = container_artifacts[subject_id]
            hash_process = run_bounded(
                ["docker", "exec", container_name, "sha256sum", target],
                timeout_seconds=15,
            )
            actual_hash = hash_process.stdout.split()[0] if hash_process.returncode == 0 else None
            post_execution_integrity.append(
                {
                    "subject_id": subject_id,
                    "target": target,
                    "expected_sha256": launch["artifact_sha256"],
                    "actual_sha256": actual_hash,
                    "matches": actual_hash == launch["artifact_sha256"],
                }
            )
            resources = launch.get("resources", [])
            if isinstance(resources, list):
                for resource in resources:
                    if not isinstance(resource, dict):
                        continue
                    resource_file = str(Path(str(resource.get("artifact_file"))).resolve())
                    resource_target = container_resources[subject_id][resource_file]
                    resource_hash_process = run_bounded(
                        ["docker", "exec", container_name, "sha256sum", resource_target],
                        timeout_seconds=15,
                    )
                    actual_resource_hash = (
                        resource_hash_process.stdout.split()[0]
                        if resource_hash_process.returncode == 0
                        else None
                    )
                    post_execution_integrity.append(
                        {
                            "subject_id": subject_id,
                            "resource_id": resource.get("resource_id"),
                            "target": resource_target,
                            "expected_sha256": resource.get("artifact_sha256"),
                            "actual_sha256": actual_resource_hash,
                            "matches": actual_resource_hash == resource.get("artifact_sha256"),
                        }
                    )
        if not all(item["matches"] for item in post_execution_integrity):
            raise BlackridgeError("sandboxed component bytes changed during execution")
    finally:
        if container_name:
            remove_argv = ["docker", "rm", "--force", container_name]
            removed = run_bounded(
                remove_argv,
                timeout_seconds=15,
                maximum_output_bytes_per_stream=65_536,
            )
            force_remove = {
                "argv": remove_argv,
                "exit_code": removed.returncode,
                "stdout": removed.stdout,
                "stderr": removed.stderr,
            }
            container_exists_after = _container_exists(container_name)

    if runtime_probe is None:
        raise BlackridgeError("sandboxed generated runtime did not produce evidence")
    sandbox_observation = {
        "image": image,
        "container_name": container_name,
        "security_options": [
            "cap-drop=ALL",
            "no-new-privileges",
            f"pids-limit={sandbox_resources.pids}",
            f"memory={memory_flag}",
            f"memory-swap={memory_flag}",
            f"cpus={cpu_flag}",
            "workload-user=65534:65534",
        ],
        "copied_components": copied,
        "post_execution_component_integrity": post_execution_integrity,
        "execution_boundary": boundary,
        "preflight": preflight,
        "cleanup": {
            "force_remove": force_remove,
            "container_exists_after": container_exists_after,
        },
    }
    if (
        force_remove is None
        or force_remove["exit_code"] != 0
        or container_exists_after is not False
    ):
        raise BlackridgeError("sandboxed generated runtime cleanup could not be confirmed")
    observations = dict(runtime_probe.observations)
    observations["sandbox"] = sandbox_observation
    request = dict(runtime_probe.request)
    request["image_ref"] = effective_image_ref
    request["resolved_image_id"] = image["resolved_id"]
    sources = list(runtime_probe.sources)
    if SWEREX_SOURCE not in sources:
        sources.append(SWEREX_SOURCE)
    return runtime_probe.model_copy(
        update={"request": request, "observations": observations, "sources": sources}
    )


class CompositionSystemProbe:
    """Solve, generate, and execute one frozen composition workload."""

    def probe(
        self,
        definition: CompositionDefinition,
        *,
        definition_file: Path,
        output_directory: Path,
        input_artifact: object,
    ) -> ProbeEvidence:
        plan = solve_composition(definition, definition_file=definition_file)
        generation: GeneratedSystem | None = None
        runtime_probe: ProbeEvidence | None = None
        warnings = list(plan.warnings)
        if plan.complete:
            generation = generate_system(
                definition,
                plan,
                definition_file=definition_file,
                output_directory=output_directory,
            )
            if definition.mode == "calibration":
                runtime_probe = run_generated_system(
                    output_directory,
                    input_artifact,
                    expected_provenance_sha256=generation.artifact_sha256["provenance.json"],
                )
                warnings.extend(runtime_probe.warnings)
            else:
                warnings.append(
                    "Production execution was not attempted because v1 has no sandbox runtime."
                )
        else:
            warnings.append("Generation and execution were not attempted for an incomplete plan.")
        return ProbeEvidence(
            probe_id=uuid4().hex,
            observed_at=datetime.now(UTC),
            provider="blackridge-composition-system-probe/1",
            subject=definition.name,
            request={
                "definition_file": str(definition_file.resolve()),
                "definition_sha256": _sha256_file(definition_file),
                "output_directory": str(output_directory.resolve()),
                "input_artifact": input_artifact,
            },
            observations={
                "probe_completed": True,
                "plan": _primitive(plan),
                "generation": _primitive(generation) if generation else None,
                "runtime": _primitive(runtime_probe) if runtime_probe else None,
            },
            sources=[COMPOSITION_SOURCE, JSON_PATCH_SOURCE, JSON_SCHEMA_SOURCE],
            warnings=warnings,
        )
