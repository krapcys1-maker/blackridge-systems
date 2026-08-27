"""Compatibility solving, provenance-locked generation, and linear system execution."""

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


class ComponentLaunch(StrictModel):
    argv: list[str] = Field(min_length=1)
    artifact_file: str = Field(min_length=1)
    artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    working_directory: str = "{definition_dir}"
    timeout_seconds: float = Field(default=30, gt=0, le=300)
    environment_allowlist: list[str] = Field(default_factory=list)


class ComponentOption(StrictModel):
    """One replaceable implementation behind a single-stream contract boundary."""

    component_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    capability_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    source_uri: str = Field(min_length=3)
    revision: str = Field(min_length=7)
    license_spdx: str = Field(min_length=2)
    integration: Literal["command-json", "python-library", "cli", "api", "oci"]
    accepts: list[str] = Field(min_length=1, max_length=1)
    produces: list[str] = Field(min_length=1, max_length=1)
    launch: ComponentLaunch | None = None
    evidence: EvidenceReference
    selection_priority: int = Field(default=100, ge=0)
    blocked_reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def command_integration_has_launch(self) -> ComponentOption:
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


class CompositionDefinition(StrictModel):
    """A frozen single-stream composition problem and all permitted choices."""

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
    output_contract: str


class CombinationObservation(StrictModel):
    component_ids: list[str]
    complete: bool
    adapter_count: int
    steps: list[PlanStep]
    unresolved_capabilities: list[str]
    terminal_contract: str


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
    queue: list[tuple[int, int, tuple[str, ...], str, frozenset[str], tuple[PlanStep, ...]]] = [
        (0, 0, (), definition.external_input, frozenset(), ())
    ]
    visited: dict[tuple[str, frozenset[str]], tuple[int, int, tuple[str, ...]]] = {}
    furthest: tuple[str, frozenset[str], tuple[PlanStep, ...]] = (
        definition.external_input,
        frozenset(),
        (),
    )

    while queue:
        adapter_count, step_count, path_key, current, executed, steps = heapq.heappop(queue)
        state = (current, executed)
        cost = (adapter_count, step_count, path_key)
        if state in visited and visited[state] <= cost:
            continue
        visited[state] = cost
        if len(executed) > len(furthest[1]) or (
            len(executed) == len(furthest[1]) and len(steps) < len(furthest[2])
        ):
            furthest = (current, executed, steps)
        if executed == required_ids and current == definition.required_output:
            return CombinationObservation(
                component_ids=sorted(selected),
                complete=True,
                adapter_count=adapter_count,
                steps=list(steps),
                unresolved_capabilities=[],
                terminal_contract=current,
            )

        actions: list[tuple[Literal["component", "adapter"], str, str, str]] = []
        for component in components:
            if component.component_id not in executed and component.accepts[0] == current:
                actions.append(
                    (
                        "component",
                        component.component_id,
                        component.accepts[0],
                        component.produces[0],
                    )
                )
        for adapter in adapters:
            if adapter.source_contract == current and adapter.target_contract != current:
                actions.append(
                    (
                        "adapter",
                        adapter.adapter_id,
                        adapter.source_contract,
                        adapter.target_contract,
                    )
                )
        actions.sort(key=lambda item: (item[0], item[1]))
        for step_type, subject_id, input_contract, output_contract in actions:
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
                input_contract=input_contract,
                output_contract=output_contract,
            )
            next_path = (*path_key, f"{step_type}:{subject_id}")
            heapq.heappush(
                queue,
                (
                    next_adapter_count,
                    step_count + 1,
                    next_path,
                    output_contract,
                    next_executed,
                    (*steps, next_step),
                ),
            )

    unresolved = sorted(
        {selected[component_id].capability_id for component_id in required_ids - furthest[1]}
    )
    return CombinationObservation(
        component_ids=sorted(selected),
        complete=False,
        adapter_count=sum(step.step_type == "adapter" for step in furthest[2]),
        steps=list(furthest[2]),
        unresolved_capabilities=unresolved,
        terminal_contract=furthest[0],
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
                runtime_step["launch"] = {
                    "argv": [
                        _expand_control_value(value, definition_directory)
                        for value in component.launch.argv
                    ],
                    "working_directory": _expand_control_value(
                        component.launch.working_directory, definition_directory
                    ),
                    "artifact_file": _expand_control_value(
                        component.launch.artifact_file, definition_directory
                    ),
                    "artifact_sha256": component.launch.artifact_sha256,
                    "timeout_seconds": component.launch.timeout_seconds,
                    "environment_allowlist": component.launch.environment_allowlist,
                }
            else:
                runtime_step["operations"] = adapter_by_id[step.subject_id].operations
            runtime_steps.append(runtime_step)

        runtime = {
            "schema_version": "1",
            "system_name": definition.name,
            "mode": definition.mode,
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
    environment["PYTHONIOENCODING"] = "utf-8"
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
    _provider: str = "blackridge-generated-linear-runtime/1",
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

    validated_steps: list[tuple[dict[str, Any], str, str, str, str]] = []
    component_controls: dict[int, tuple[dict[str, Any], Path, str, dict[str, object]]] = {}
    component_integrity_failures: dict[int, str] = {}
    for step_index, raw_step in enumerate(steps):
        if not isinstance(raw_step, dict):
            raise BlackridgeError("generated runtime step is not an object")
        subject_id = raw_step.get("subject_id")
        step_type = raw_step.get("step_type")
        input_contract = raw_step.get("input_contract")
        output_contract = raw_step.get("output_contract")
        if not (
            isinstance(subject_id, str)
            and isinstance(step_type, str)
            and isinstance(input_contract, str)
            and isinstance(output_contract, str)
        ):
            raise BlackridgeError("generated runtime step fields are invalid")
        validated_steps.append((raw_step, subject_id, step_type, input_contract, output_contract))
        if step_type != "component":
            continue
        launch = raw_step.get("launch")
        if not isinstance(launch, dict):
            raise BlackridgeError(f"component step has no launch control: {subject_id}")
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

    current_contract = runtime.get("external_input")
    required_output = runtime.get("required_output")
    if not isinstance(current_contract, str) or not isinstance(required_output, str):
        raise BlackridgeError("generated runtime contract endpoints are invalid")
    artifact = input_artifact
    initial_errors = _validation_errors(schemas[current_contract], artifact)
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
        input_contract,
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
        if input_contract != current_contract:
            failed = True
            failure_reason = (
                f"step {subject_id} expected {input_contract} but runtime held {current_contract}"
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
        before_errors = _validation_errors(schemas[current_contract], artifact)
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

        observation: dict[str, Any] = {
            "subject_id": subject_id,
            "step_type": step_type,
            "status": "failed",
            "input_contract": input_contract,
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
                artifact = produced
                current_contract = output_contract
            elif after_errors:
                failure_reason = f"step {subject_id} produced an invalid output artifact"
        if observation["status"] != "completed":
            failed = True
            observation["reason"] = failure_reason
        step_observations.append(observation)

    final_errors = (
        _validation_errors(schemas[required_output], artifact)
        if current_contract == required_output
        else []
    )
    completed = not failed and current_contract == required_output and not final_errors
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
            "final_contract": current_contract,
            "required_output": required_output,
            "final_artifact": artifact,
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
    subject_id: str,
) -> list[str]:
    """Map one locked Python component launch without trusting host absolute paths."""

    effective_argv: list[str] = []
    python_names = {"python", "python.exe", "python3", "python3.exe"}
    for index, value in enumerate(declared_argv):
        argument = str(value)
        if index == 0 and Path(argument).name.lower() in python_names:
            effective_argv.append("python")
        elif str(Path(argument).resolve()) == artifact_file:
            effective_argv.append(container_artifact)
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
    image_ref: str = "blackridge/swerex-runtime:1.4.0",
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
    image = inspect_local_image(image_ref)
    deployment = DockerDeployment(
        image=image["resolved_id"],
        pull="never",
        remove_container=True,
        startup_timeout=180,
        docker_args=[
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit=256",
            "--memory=1g",
            "--memory-swap=1g",
            "--cpus=2",
        ],
        logger=adapter._logger(),
    )
    container_name: str | None = None
    copied: list[dict[str, object]] = []
    container_artifacts: dict[str, str] = {}
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
                command=["mkdir", "-p", "/workspace/components"],
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
            and cgroup.get("memory_max") == "1073741824",
            "memory_swap_disabled": isinstance(cgroup, dict)
            and cgroup.get("memory_swap_max") == "0",
            "pids_limit_exact": isinstance(cgroup, dict) and cgroup.get("pids_max") == "256",
            "cpu_limit_exact": isinstance(cgroup, dict)
            and cgroup.get("cpu_max") == "200000 100000",
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
            "pids-limit=256",
            "memory=1g",
            "memory-swap=1g",
            "cpus=2",
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
    request["image_ref"] = image_ref
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
